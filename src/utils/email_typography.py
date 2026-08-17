"""邮件排版的共享字体契约。

Cambria / Times New Roman 使用齐高数字；中文字符自然回落到各平台宋体。
不要把 Georgia 放在前面：它的旧式数字会让 3、4、5、7、9 下沉，在 QQ 邮箱
不支持 ``lnum`` 切换时尤其明显。
"""

EMAIL_EDITORIAL_SERIF = (
    "Cambria,'Times New Roman','Noto Serif SC','Songti SC','SimSun',serif"
)
EMAIL_NUMERIC_FEATURES = (
    "font-variant-numeric:lining-nums tabular-nums;"
    "font-feature-settings:'lnum' 1,'tnum' 1;"
)
