"""
M5 候选图策展处理:汇总 → 粗筛 → 并发 HEAD 验证 → 输出 JSON。
图源:Pexels(Unsplash 反爬不可达,已切源)。
CDN URL 模式:
  https://images.pexels.com/photos/{id}/pexels-photo-{id}.jpeg?auto=compress&cs=tinysrgb&w=1280&h=400&fit=crop
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 元数据粗筛黑名单(slug 中出现任一即淘汰)
BLOCKLIST = {
    "sunset", "sunrise", "dawn", "dusk", "twilight",
    "golden-hour", "golden-light", "golden-sunrise", "golden-sun",
    "woman", "women", "man", "men", "girl", "boy", "people", "person",
    "wedding", "portrait", "topless", "shirtless", "running", "walking", "explorer",
    "neon", "city-skyline-night", "nightlife",
    "colorful", "vibrant", "rainbow", "flower-field", "flower-garden",
    "deer", "roe-deer", "horse", "cow", "wildlife-close",
    "ski-lift", "tractor", "abandoned-tractor", "christmas-lights",
    "crepuscular-rays", "sunbeams", "sun-rays",
    "orange-trees", "pink-cherry",
    "still-life", "glass-bottle", "ceramic-cup", "leafless-tree-low-key",
    "low-angle", "close-up-photography", "selective-focus", "soft-focus", "abstract-artistic",
    "city", "town", "village", "houses", "highway",  # 排除明显城市/村庄主体
    "london-streets", "skyline",
    "ship", "boat", "boats",
    "car", "road-vehicle",
    "aurora-borealis",  # 极光太特别,不想要
    "transmission-tower", "tower-on-winter", "small-tower", "pagoda",
}

# slug 不允许出现的整词(更严格)
EXACT_BLOCK_TOKENS = {"man", "woman", "girl", "boy", "topless"}

# 候选数据:(id, slug, season, query_hint)
CANDIDATES = [
    # ---------- SPRING ----------
    ("30882618", "misty-forest-landscape-with-dense-fog", "spring", "spring fog"),
    ("4827", "nature-forest-trees-fog", "spring", "spring fog"),
    ("36646592", "moody-misty-forest-landscape-with-evergreen-trees", "spring", "spring fog"),
    ("7919", "aerial-photography-of-cloudy-mountain", "spring", "spring fog"),
    ("28896963", "misty-meadow-with-yellow-wildflowers-at-dawn", "spring", "spring fog"),
    ("35001488", "misty-forest-tall-trees-in-atmospheric-fog", "spring", "spring fog"),
    ("1287083", "trees-surrounded-by-fogs-in-the-forest", "spring", "spring fog"),
    ("158672", "trees-with-fog", "spring", "spring fog"),
    ("24536427", "fog-over-lake-under-hill", "spring", "spring fog"),
    ("59688", "mountains-foggy-misty-fog", "spring", "spring fog"),
    ("28966860", "misty-morning-landscape-in-tranquil-countryside", "spring", "misty mountain"),
    ("10770234", "silhouette-of-mountains-under-the-blue-sky", "spring", "misty mountain"),
    ("31828684", "serene-mountain-sunrise-over-misty-valleys", "spring", "misty mountain"),
    ("29582943", "serene-mountain-dawn-with-misty-clouds", "spring", "misty mountain"),
    ("29070548", "serene-frosty-morning-in-a-misty-bog-landscape", "spring", "misty mountain"),
    ("1183099", "photography-of-mountains-under-cloudy-sky", "spring", "misty mountain"),
    ("9291023", "mountains-at-sunrise", "spring", "misty mountain"),
    ("733100", "sunset-view-of-mountains", "spring", "misty mountain"),
    ("33312427", "aerial-view-of-misty-mountain-range-landscape", "spring", "misty mountain"),
    ("4497588", "quiet-green-forest-on-sunny-spring-day", "spring", "new green"),
    ("149521", "white-and-green-flower-plant-in-leafless-tress", "spring", "new green"),
    ("795622", "green-forest", "spring", "new green"),
    ("62301", "landscape-nature-sky-spring", "spring", "new green"),
    ("20523396", "green-coniferous-forest", "spring", "new green"),
    ("17005430", "river-flowing-in-green-nature-landscape", "spring", "new green"),
    ("1770809", "green-grass-near-trees", "spring", "new green"),
    ("17203268", "green-trees-in-forest", "spring", "new green"),
    ("1226302", "close-up-photo-of-green-fern-leaf", "spring", "new green"),
    ("623409", "silhouette-photo-of-green-trees-under-crepuscular-rays", "spring", "new green"),
    ("21753035", "rain-clouds-over-mountains", "spring", "spring rain"),
    ("29223512", "moody-forest-landscape-at-twilight", "spring", "spring rain"),
    ("31947949", "moody-dense-forest-under-cloudy-sky", "spring", "spring rain"),
    ("6172330", "wetlands-in-spring", "spring", "spring rain"),
    ("29834890", "moody-black-and-white-landscape-in-bolu-turkey", "spring", "spring rain"),
    ("1363873", "scenic-view-of-mountain-under-cloudy-sky", "spring", "spring rain"),
    ("8708732", "grass-field-under-the-cloudy-sky", "spring", "spring rain"),
    ("7316810", "pink-cherry-blossom-in-black-background", "spring", "cherry monochrome"),
    ("31145708", "monochrome-cherry-blossoms-in-soft-focus", "spring", "cherry monochrome"),
    ("6195987", "light-dawn-landscape-sunset", "spring", "spring meadow"),
    ("28896609", "tranquil-meadow-landscape-at-sunrise", "spring", "spring meadow"),
    ("32176280", "serene-misty-morning-in-grassy-meadow", "spring", "spring meadow"),
    ("7789192", "foggy-morning-in-countryside-field", "spring", "spring meadow"),
    ("31544471", "beautiful-sunrise-over-misty-meadow", "spring", "spring meadow"),
    ("4488071", "grass-field-near-forest-in-overcast-weather", "spring", "overcast field"),
    ("102160", "landscape-nature-sky-clouds", "spring", "overcast field"),
    ("55766", "wheat-field-under-gray-sky", "spring", "overcast field"),
    ("12488674", "dark-image-of-a-mountain-landscape-in-a-fog-and-winding-road", "spring", "overcast field"),
    ("5850135", "highway-and-forest-under-overcast-sky", "spring", "overcast field"),
    ("33147349", "serene-sunrise-over-misty-riverside-landscape", "spring", "misty river"),
    ("34555740", "misty-morning-landscape-in-poland-countryside", "spring", "misty river"),
    ("13646192", "man-posing-topless", "spring", "misty river"),
    ("14376356", "a-small-bridge-over-a-river-at-dawn", "spring", "misty river"),
    ("30982497", "misty-forest-pathway-in-serene-morning-light", "spring", "misty river"),
    ("345043", "body-of-water", "spring", "misty river"),
    ("30712585", "golden-sunrise-over-misty-mountain-range", "spring", "misty river"),
    ("4210234", "misty-flower-field", "spring", "misty river"),
    ("28491928", "scenic-sunrise-over-mississippi-river-in-wabasha", "spring", "misty river"),
    # ---------- SUMMER ----------
    ("1463668", "view-of-ocean-covered-with-fog", "summer", "ocean fog"),
    ("4640990", "fog-over-sea-near-shore", "summer", "ocean fog"),
    ("10278716", "fog-over-ocean", "summer", "ocean fog"),
    ("18050899", "scenic-view-of-sea-and-coastline-under-overcast-sky", "summer", "ocean fog"),
    ("10907104", "a-ship-on-a-foggy-ocean", "summer", "ocean fog"),
    ("28770440", "foggy-cliffside-overlooking-calm-ocean-waters", "summer", "ocean fog"),
    ("17492079", "fog-over-village-on-sea-shore", "summer", "ocean fog"),
    ("448748", "blue-body-of-water-with-fog", "summer", "ocean fog"),
    ("10392336", "foggy-london-streets", "summer", "ocean fog"),
    ("18983204", "fog-over-beach-and-cliff-on-sea-shore", "summer", "ocean fog"),
    ("29673131", "lush-green-moss-in-brazilian-forest-light", "summer", "deep forest moss"),
    ("240040", "forest", "summer", "deep forest moss"),
    ("11932114", "moss-in-dark-forest", "summer", "deep forest moss"),
    ("13728503", "ocean-view-under-gray-sky", "summer", "overcast sea"),
    ("19978828", "sunset-over-sea-coast", "summer", "overcast sea"),
    ("5273591", "overcast-over-sea-shore-and-mountains", "summer", "overcast sea"),
    ("4775483", "rock-on-sea-in-overcast-weather-in-evening", "summer", "overcast sea"),
    ("34053957", "scenic-rock-jetty-extending-into-calm-blue-sea", "summer", "overcast sea"),
    ("5098158", "rocky-cliff-above-sea-in-overcast", "summer", "overcast sea"),
    ("5331812", "empty-beach-of-ocean-in-overcast-weather", "summer", "overcast sea"),
    ("9578029", "brown-rocky-mountain-beside-the-beach", "summer", "overcast sea"),
    ("18429123", "beach-at-dusk-and-overcast", "summer", "overcast sea"),
    ("235637", "grey-rocks-on-river-landscape-photography", "summer", "mossy rocks"),
    ("18703061", "view-of-a-rocky-stream-in-the-forest", "summer", "mossy rocks"),
    ("7520331", "water-flowing-on-a-shallow-rocky-river", "summer", "mossy rocks"),
    ("8048466", "waterfalls-cascading-on-mossy-rocks-in-the-forest", "summer", "mossy rocks"),
    ("3800074", "time-lapse-photo-of-river-between-mossy-rocks", "summer", "mossy rocks"),
    ("216757", "body-of-water-between-black-rock-artwork", "summer", "mossy rocks"),
    ("5777919", "waterfalls-in-the-forest", "summer", "mossy rocks"),
    ("1271620", "creek-in-a-forest", "summer", "mossy rocks"),
    ("1964", "nature-water-forest-brook", "summer", "mossy rocks"),
    ("34253909", "mountain-stream-flowing-over-mossy-rocks", "summer", "mossy rocks"),
    ("28640152", "serene-sunrise-over-misty-lake-landscape", "summer", "misty lake"),
    ("24713006", "sunrise-over-misty-lake", "summer", "misty lake"),
    ("7561149", "coastal-town-in-fog", "summer", "coastal cliff"),
    ("1803", "mountains-rocks-fog-foggy", "summer", "coastal cliff"),
    ("15380646", "photo-of-people-walking-on-a-cliff-in-fog", "summer", "coastal cliff"),
    ("3011846", "foggy-cliff", "summer", "coastal cliff"),
    ("12871045", "mountain-with-cliffs-in-a-fog", "summer", "coastal cliff"),
    ("14471495", "black-and-white-landscape-of-mountains-and-water-in-fog", "summer", "coastal cliff"),
    ("9156056", "scenic-view-of-a-coastal-cliff", "summer", "coastal cliff"),
    ("1367192", "photo-of-foggy-forest", "summer", "pine fog"),
    ("1366921", "photo-of-forest-covered-by-fog", "summer", "pine fog"),
    ("18591164", "a-foggy-landscape-with-trees-and-fog", "summer", "pine fog"),
    ("167699", "green-pine-trees-covered-with-fogs-under-white-sky-during-daytime", "summer", "pine fog"),
    ("9754", "forest-mountains-fog-clouds", "summer", "pine fog"),
    ("3975366", "green-pine-trees-covered-with-fog", "summer", "pine fog"),
    ("670782", "early-morning-fog-forest-haze", "summer", "pine fog"),
    ("5049", "forest-trees-fog-foggy", "summer", "pine fog"),
    ("12264406", "reflection-of-the-sky-in-a-still-lake", "summer", "lake reflection"),
    ("13364090", "reflection-of-cloudy-sky-on-lake-surface", "summer", "lake reflection"),
    ("17180777", "mountains-with-reflection-in-lake", "summer", "lake reflection"),
    ("5097949", "reflection-of-a-cloudy-sky-on-a-lake", "summer", "lake reflection"),
    ("1557652", "tree-with-reflection-on-body-of-water", "summer", "lake reflection"),
    ("15745269", "reflection-of-trees-and-mountains-in-the-lake", "summer", "lake reflection"),
    ("19036832", "mountain-reflection-in-lake", "summer", "lake reflection"),
    ("8389188", "trees-reflection-on-a-lake", "summer", "lake reflection"),
    ("28481987", "serene-foggy-lake-with-tree-reflection", "summer", "lake reflection"),
    ("247110", "storm-clouds-over-field-during-sunset", "summer", "storm clouds"),
    ("15211444", "a-storm-cloud-above-a-field", "summer", "storm clouds"),
    ("355441", "grass-field-below-clouds", "summer", "storm clouds"),
    ("65295", "clouds-field-storm-thunderstorm", "summer", "storm clouds"),
    ("2553399", "grey-storm-clouds-over-houses-in-slope-of-hills", "summer", "storm clouds"),
    ("8903157", "dark-heavy-clouds-over-the-grass-land", "summer", "storm clouds"),
    ("1822996", "electric-lines-over-cloudy-sky", "summer", "storm clouds"),
    ("16277399", "view-of-dark-storm-clouds", "summer", "storm clouds"),
    ("16880145", "storm-clouds-over-a-countryside", "summer", "storm clouds"),
    ("13258137", "heavy-dark-clouds-over-mountains", "summer", "storm clouds"),
    # ---------- AUTUMN ----------
    ("9892165", "trees-in-the-middle-of-dry-fields-and-hills", "autumn", "autumn dry grass"),
    ("16297902", "close-up-of-dry-grass-on-a-field", "autumn", "autumn dry grass"),
    ("3730164", "dry-grass-in-field-during-sunset", "autumn", "autumn dry grass"),
    ("17242182", "misty-countryside-landscape-with-grass-field-covered-with-morning-dew", "autumn", "autumn dry grass"),
    ("28956720", "colorful-autumn-leaves-on-grass", "autumn", "autumn dry grass"),
    ("773583", "landscape-photography-green-grass-field-beside-dark-foggy-forest-during-golden-hour", "autumn", "autumn dry grass"),
    ("10111157", "fallen-leaves-on-green-grass", "autumn", "autumn dry grass"),
    ("174614", "autumn-grass-background-grass", "autumn", "autumn dry grass"),
    ("5889428", "clear-river-flowing-among-dry-grass-and-trees", "autumn", "autumn dry grass"),
    ("5662136", "bright-autumn-leaves-on-grass-lawn-in-park", "autumn", "autumn dry grass"),
    ("13739181", "dry-landscape-with-grass-and-mountains", "autumn", "dry grass minimal"),
    ("17399019", "landscape-of-a-grass-field-and-hills-under-blue-sky", "autumn", "dry grass minimal"),
    ("2816057", "photo-of-grass-field-during-daytime", "autumn", "dry grass minimal"),
    ("65281", "landscape-nature-grass-meadow", "autumn", "dry grass minimal"),
    ("3771809", "happy-young-woman-running-in-field", "autumn", "dry grass minimal"),
    ("5876817", "small-tower-near-autumn-forest", "autumn", "dry grass minimal"),
    ("380012", "background-dry-grass-landscape-yellow-grass", "autumn", "dry grass minimal"),
    ("28352672", "an-aerial-view-of-a-field-with-crops", "autumn", "harvested field"),
    ("5876603", "dry-wheat-growing-on-countryside-field", "autumn", "harvested field"),
    ("7626143", "a-field-with-wheat-crops", "autumn", "harvested field"),
    ("19685051", "roe-deer-standing-in-a-foggy-autumn-field-at-dusk", "autumn", "harvested field"),
    ("13957215", "big-brown-tree-on-brown-grass-field-under-the-white-clouds", "autumn", "harvested field"),
    ("53435", "green-tree-on-grass-field-during-daytime", "autumn", "harvested field"),
    ("1227513", "photo-of-grass-field", "autumn", "harvested field"),
    ("34063300", "golden-wheat-field-under-clear-blue-sky", "autumn", "harvested field"),
    ("1048039", "green-grass-field-under-white-clouds", "autumn", "harvested field"),
    ("1933180", "grey-cloudy-sunset-sky-over-the-farm-field", "autumn", "harvested field"),
    ("10817836", "forest-path-in-fog", "autumn", "autumn fog forest"),
    ("1102908", "foggy-path", "autumn", "autumn fog forest"),
    ("10239298", "a-foggy-forest-during-autumn", "autumn", "autumn fog forest"),
    ("34889618", "foggy-road-through-a-dark-forest-in-autumn", "autumn", "autumn fog forest"),
    ("5868172", "fog-over-dark-autumn-forest", "autumn", "autumn fog forest"),
    ("5837865", "narrow-pathway-between-bright-autumn-trees-in-fog", "autumn", "autumn fog forest"),
    ("9782162", "photo-of-a-forest-at-sunrise", "autumn", "autumn fog forest"),
    ("15576547", "foggy-road-between-trees-in-autumn", "autumn", "autumn fog forest"),
    ("1655901", "landscape-photo-of-forest", "autumn", "autumn fog forest"),
    ("29443875", "autumn-birch-tree-in-misty-forest-scene", "autumn", "birch autumn"),
    ("28927824", "abstract-artistic-image-of-birch-tree-forest", "autumn", "birch autumn"),
    ("28471869", "peaceful-birch-tree-pathway-in-autumn-forest", "autumn", "birch autumn"),
    ("29136939", "misty-forest-with-birch-and-pine-trees", "autumn", "birch autumn"),
    ("29120792", "foggy-birch-tree-in-a-misty-autumn-landscape", "autumn", "birch autumn"),
    ("15846861", "birch-forest-in-autumn", "autumn", "birch autumn"),
    ("19957279", "birch-tree-in-autumn-foliage", "autumn", "birch autumn"),
    ("16512172", "birch-trees-in-autumn-with-yellow-leaves", "autumn", "birch autumn"),
    ("29173938", "rustic-autumn-birch-tree-by-a-tranquil-lake", "autumn", "birch autumn"),
    ("1590549", "selective-focus-photography-of-dried-leaves", "autumn", "birch autumn"),
    ("29715252", "misty-forest-in-autumn-with-bare-trees", "autumn", "bare trees autumn"),
    ("1640882", "dried-leaves-under-a-tree-on-park-with-fogs-landscape-photography", "autumn", "bare trees autumn"),
    ("78940", "autumn-field-fog-trees", "autumn", "bare trees autumn"),
    ("13622968", "grass-field-and-trees-covered-in-fog", "autumn", "bare trees autumn"),
    ("19533133", "fog-over-a-lake-at-sunset", "autumn", "autumn lake"),
    ("12527037", "lake-surrounded-by-trees-covered-with-fog", "autumn", "autumn lake"),
    ("34675744", "serene-autumn-reflection-on-calm-lake-waters", "autumn", "autumn lake"),
    ("6435268", "gyeongbokgung-palace-in-the-middle-of-the-lake-surrounded-with-autumn-trees", "autumn", "autumn lake"),
    ("35004410", "tranquil-autumn-landscape-with-hillside-and-lake", "autumn", "autumn lake"),
    ("29479873", "serene-autumn-lake-with-fall-foliage-reflection", "autumn", "autumn lake"),
    ("89403", "lake-with-fog-under-dark-blue-sky-photography", "autumn", "autumn lake"),
    ("28961234", "misty-autumn-morning-in-a-forest-clearing", "autumn", "misty autumn"),
    ("6017053", "forest-on-a-foggy-morning-with-sun-rays-and-shadows", "autumn", "misty autumn"),
    ("13149428", "field-with-trees-in-morning-mist-and-sunbeams", "autumn", "misty autumn"),
    ("28811877", "misty-pine-forest-in-autumn-colors", "autumn", "misty autumn"),
    ("29579812", "misty-forest-pathway-in-early-morning-light", "autumn", "misty autumn"),
    ("34482318", "moody-scottish-highland-landscape-in-autumn", "autumn", "moody autumn"),
    # ---------- WINTER ----------
    ("691668", "landscape-photography-of-mountains-covered-in-snow", "winter", "snow nordic"),
    ("1366919", "landscape-photography-of-snowy-mountain", "winter", "snow nordic"),
    ("15382", "mountains-landscape-winter-snow", "winter", "snow nordic"),
    ("34348785", "scenic-winter-view-of-norwegian-fjord-landscape", "winter", "snow nordic"),
    ("35375041", "minimalist-winter-scene-with-leaf-and-snow", "winter", "snow nordic"),
    ("15506561", "tower-on-winter-landscape", "winter", "snow nordic"),
    ("6530841", "winter-landscape", "winter", "snow nordic"),
    ("30816460", "scenic-snowy-mountain-landscape-with-ski-lift", "winter", "snow nordic"),
    ("29823042", "scenic-winter-road-through-snowy-norwegian-landscape", "winter", "snow nordic"),
    ("52710", "landscape-photography-of-mountain-with-snow", "winter", "snow nordic"),
    ("18542025", "winter-forest-in-the-fog", "winter", "snow fog"),
    ("4913511", "snowy-forest-with-high-trees-on-foggy-day", "winter", "snow fog"),
    ("5222", "snow-mountains-forest-winter", "winter", "snow fog"),
    ("10408415", "aerial-shot-of-a-foggy-forest", "winter", "snow fog"),
    ("691571", "photo-of-orange-trees", "winter", "snow fog"),
    ("20142761", "dark-mountains-with-snow-and-fog", "winter", "snow fog"),
    ("6752125", "snow-covered-road-near-the-trees", "winter", "snow fog"),
    ("773953", "white-snowy-environment-with-pine-trees", "winter", "snow fog"),
    ("4406183", "cloudy-sky-over-snowy-mountains-and-lush-forest", "winter", "snow fog"),
    ("6397400", "cloudy-sky-over-lush-coniferous-forest-covered-with-snow-in-winter", "winter", "snow fog"),
    ("1367188", "monochrome-photography-of-mountain-covered-by-clouds", "winter", "monochrome winter"),
    ("1840101", "snow-covered-rocky-mountain", "winter", "monochrome winter"),
    ("355770", "mountain-covered-with-snow-digital-wallpaper", "winter", "monochrome winter"),
    ("1446713", "monochrome-photography-of-mountain", "winter", "monochrome winter"),
    ("14461667", "grayscale-photo-of-mountain-peak-covered-with-snow", "winter", "monochrome winter"),
    ("2683746", "monochrome-photo-of-mountains", "winter", "monochrome winter"),
    ("2479026", "monochrome-photo-of-mountain", "winter", "monochrome winter"),
    ("17475755", "white-snow-and-cloud-in-mountains", "winter", "monochrome winter"),
    ("5570439", "black-and-white-photo-of-the-snowy-mountains", "winter", "monochrome winter"),
    ("371649", "mountains-covered-with-snow", "winter", "monochrome winter"),
    ("14815629", "shirtless-man-on-a-frozen-lake", "winter", "frozen lake"),
    ("20306445", "frozen-lake-among-evergreen-trees", "winter", "frozen lake"),
    ("4318217", "frozen-lake-surrounded-with-wooded-mountains-in-snowy-winter", "winter", "frozen lake"),
    ("2004390", "leafless-tree", "winter", "frozen lake"),
    ("5892621", "the-frozen-lake-louise-in-alberta-canada-during-winter", "winter", "frozen lake"),
    ("327424", "view-of-frozen-lake-during-sunset", "winter", "frozen lake"),
    ("2004388", "body-of-water-across-white-mountain", "winter", "frozen lake"),
    ("29900182", "snow-covered-forest-and-frozen-lake-scenic-view", "winter", "frozen lake"),
    ("327434", "calm-waters-clouds-cold-country", "winter", "frozen lake"),
    ("6830964", "explorer-walking-on-frozen-lake", "winter", "frozen lake"),
    ("3509418", "white-snow-field", "winter", "snow field minimal"),
    ("957015", "bare-tree-with-ground-covered-by-snow", "winter", "snow field minimal"),
    ("15150340", "bare-trees-on-snow-covered-ground", "winter", "snow field minimal"),
    ("35617820", "aerial-view-of-snow-covered-farmland-in-winter", "winter", "snow field minimal"),
    ("11104472", "trees-surrounding-a-snow-covered-field", "winter", "snow field minimal"),
    ("10819629", "a-snow-covered-field-near-the-beach", "winter", "snow field minimal"),
    ("11180715", "a-bare-tree-on-a-snow-covered-field", "winter", "snow field minimal"),
    ("688660", "landscape-photography-of-snow-pathway-between-trees-during-winter", "winter", "snow field minimal"),
    ("6500181", "a-woman-in-gray-winter-jacket-standing-on-a-snow-covered-field", "winter", "snow field minimal"),
    ("19296658", "beautiful-snow-covered-pine-trees-in-a-winter-forest", "winter", "pine snow"),
    ("67820", "snow-on-pine-tree-leaves", "winter", "pine snow"),
    ("801787", "pine-trees-covered-with-snow", "winter", "pine snow"),
    ("30663486", "scenic-forest-pathway-with-pine-trees-in-winter", "winter", "pine snow"),
    ("4946941", "coniferous-trees-covered-with-snow-in-sunny-winter-day", "winter", "pine snow"),
    ("10534053", "a-snow-covered-pine-tree-with-christmas-lights", "winter", "pine snow"),
    ("5097652", "a-snow-covered-pathway-between-pine-trees", "winter", "pine snow"),
    ("21348382", "coniferous-trees-covered-with-snow", "winter", "pine snow"),
    ("904382", "snow-covered-pine-trees-under-cloudy-sky", "winter", "pine snow"),
    ("18416001", "scenic-panorama-of-a-mountain-with-peak-hidden-in-fog", "winter", "winter peak fog"),
    ("2365457", "snow-covered-mountain", "winter", "winter peak fog"),
    ("10346402", "gray-and-black-mountain-in-fog", "winter", "winter peak fog"),
    ("18145514", "mountain-covered-in-fog", "winter", "winter peak fog"),
    ("14734744", "gray-car-parked-on-snow-covered-ground", "winter", "gray winter sky"),
    ("3732527", "leafless-tree-under-gray-sky", "winter", "gray winter sky"),
    # ---------- ROUND 2 supplement (spring 24 / autumn 6 / winter 1) ----------
    ("30725259", "serene-lake-with-reflective-trees-under-cloudy-sky", "spring", "cloudy lake"),
    ("9501329", "a-reflection-of-trees-on-a-calm-lake", "spring", "cloudy lake"),
    ("9188430", "reflection-of-the-sky-on-lake", "spring", "cloudy lake"),
    ("16065845", "green-hillside-under-cloudy-sky", "spring", "green hillside"),
    ("30654271", "misty-rural-hillside-with-overcast-skies", "spring", "green hillside"),
    ("9268741", "dark-landscape-with-river-in-a-valley-and-mountain-peaks-in-fog", "spring", "green hillside"),
    ("5370215", "green-grassy-hills-in-countryside-on-overcast-day", "spring", "green hillside"),
    ("12797727", "fog-and-overcast-sky-above-mountains", "spring", "green hillside"),
    ("14808128", "clouds-over-forest-on-hillside", "spring", "green hillside"),
    ("45222", "mountain-with-green-leaved-trees-surrounded-by-fog-during-daytime", "spring", "green hillside"),
    ("5407567", "fog-over-green-forest-in-mountains", "spring", "green hillside"),
    ("16181969", "rain-clouds-over-green-hills", "spring", "green hillside"),
    ("4500037", "forest-in-foggy-morning-in-summer", "spring", "foggy farmland"),
    ("29223951", "serene-countryside-morning-with-mist-and-fog", "spring", "foggy farmland"),
    ("4114237", "weeping-willow-tree-near-body-of-water", "spring", "weeping willow"),
    ("35854904", "misty-pine-forest-in-serene-morning-light", "spring", "gray morning"),
    ("32176277", "misty-morning-by-a-quiet-riverside-landscape", "spring", "gray morning"),
    ("4468715", "summer-morning-in-calm-forest", "spring", "gray morning"),
    ("10622712", "calm-mountain-landscape-with-fog-black-and-white", "spring", "gray morning"),
    ("11575077", "calm-sea-in-mountains-landscape", "spring", "gray morning"),
    ("4406333", "fog-over-valley-with-high-trees", "spring", "spring valley"),
    ("28617613", "misty-hills-and-foggy-forest-in-munnar-india", "spring", "spring valley"),
    ("19497735", "mountain-valley-in-fog", "spring", "spring valley"),
    ("14691271", "hills-in-a-mountain-valley-covered-with-fog", "spring", "spring valley"),
    ("28551402", "misty-landscape-with-trees-and-fog-rolling-hills", "autumn", "autumn rolling"),
    ("18897224", "autumn-mountain-landscape-in-fog", "autumn", "autumn rolling"),
    ("10012495", "photo-of-a-forest-with-mist", "autumn", "autumn moody pines"),
    ("34472001", "majestic-autumn-landscape-with-foggy-mountain-view", "autumn", "autumn moody pines"),
    ("18842494", "a-broken-tree-on-a-meadow-in-the-forest-in-autumn", "autumn", "autumn moody pines"),
    ("29562214", "misty-autumn-forest-in-vermont", "autumn", "autumn moody pines"),
    ("1888403", "bare-tree-on-snow", "winter", "snow tree quiet"),
]


def is_blocked(slug: str) -> tuple[bool, str]:
    s = slug.lower()
    for term in BLOCKLIST:
        if term in s:
            return True, term
    # 整词检测
    tokens = re.split(r"[-_]", s)
    for t in tokens:
        if t in EXACT_BLOCK_TOKENS:
            return True, f"token:{t}"
    return False, ""


# 显式禁用代理(本机 HTTPS_PROXY 指向不通 Pexels 的代理)
import os  # noqa: E402

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def head_check(photo_id: str, timeout: int = 12, retries: int = 2) -> tuple[bool, int, int]:
    """返回 (ok, http_code, content_length)。带重试,处理 cloudflare 偶发 reset。"""
    url = (
        f"https://images.pexels.com/photos/{photo_id}/pexels-photo-{photo_id}.jpeg"
        f"?auto=compress&cs=tinysrgb&w=1280&h=400&fit=crop"
    )
    last_code = 0
    for _attempt in range(retries + 1):
        req = urllib.request.Request(
            url,
            method="HEAD",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
                "Accept": "image/jpeg,image/*,*/*",
            },
        )
        try:
            with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
                cl = int(resp.headers.get("content-length", "0"))
                return resp.status == 200, resp.status, cl
        except urllib.error.HTTPError as e:
            last_code = e.code
            if e.code == 404:
                return False, 404, 0  # 真 404 不重试
        except Exception:
            last_code = 0
    return False, last_code, 0


def main() -> int:
    # 去重
    seen = set()
    deduped = []
    for c in CANDIDATES:
        if c[0] in seen:
            continue
        seen.add(c[0])
        deduped.append(c)
    print(f"raw candidates: {len(CANDIDATES)} → deduped: {len(deduped)}")

    # 粗筛
    passed = []
    blocked_records = []
    for pid, slug, season, hint in deduped:
        blocked, reason = is_blocked(slug)
        if blocked:
            blocked_records.append((pid, slug, season, reason))
        else:
            passed.append((pid, slug, season, hint))
    print(f"blocked by metadata: {len(blocked_records)}")
    print(f"passed metadata: {len(passed)}")

    # 按季节统计
    from collections import Counter
    by_season = Counter(p[2] for p in passed)
    print(f"by season after metadata filter: {dict(by_season)}")

    # 并发 HEAD 验证
    print(f"\nstart HEAD validation, {len(passed)} URLs, max 5 workers (gentle on cloudflare)...")
    results = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        future_map = {ex.submit(head_check, p[0]): p for p in passed}
        for i, fut in enumerate(as_completed(future_map)):
            entry = future_map[fut]
            ok, code, cl = fut.result()
            results.append((entry, ok, code, cl))
            if (i + 1) % 20 == 0:
                print(f"  {i+1}/{len(passed)} done")

    valid = [(e, cl) for e, ok, code, cl in results if ok]
    invalid = [(e, code) for e, ok, code, cl in results if not ok]
    print(f"\nvalid (200 OK): {len(valid)}")
    print(f"invalid: {len(invalid)}")
    for e, code in invalid:
        print(f"  ✗ {e[0]} [{e[2]}] {e[1]} → {code}")

    by_season_valid = Counter(e[2] for e, _ in valid)
    print(f"\nby season after HEAD validation: {dict(by_season_valid)}")

    # 输出
    out = {"valid": [], "blocked": [], "invalid": []}
    for (pid, slug, season, hint), cl in valid:
        out["valid"].append({"id": pid, "slug": slug, "season": season, "query_hint": hint, "size_hint": cl})
    for pid, slug, season, reason in blocked_records:
        out["blocked"].append({"id": pid, "slug": slug, "season": season, "reason": reason})
    for entry, code in invalid:
        out["invalid"].append({"id": entry[0], "slug": entry[1], "season": entry[2], "code": code})

    Path("/tmp/curate_result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print("\nwrote /tmp/curate_result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
