"""邮件排版的共享字体契约。

Georgia 放在中文字体之前：它负责西文和数字，中文字符则自然回落到各平台的
宋体。这样无需远程字体，也能让 QQ、Apple Mail 与浏览器预览中的数字保持一致。
"""

EMAIL_EDITORIAL_SERIF = "Georgia,'Noto Serif SC','Songti SC','SimSun',serif"
EMAIL_NUMERIC_FEATURES = (
    "font-variant-numeric:lining-nums tabular-nums;"
    "font-feature-settings:'lnum' 1,'tnum' 1;"
)
