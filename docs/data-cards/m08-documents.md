# M08 合成企业文档数据卡

来源：`document_samples.py` 中人工编写的15个教学模板，两个虚构租户，CRM/HCM/SCM各5类主题。规则、阈值和流程完全虚构，不是企业制度、法律意见或真实个人数据。没有从互联网下载业务文档。

默认30篇（两租户各15篇）；300/1000篇用于演示文件规模，循环复用模板，不代表更广的知识覆盖。每篇带明确文档标识，版本从1开始；mixed按序轮换Markdown/PDF/DOCX，md模式不依赖第三方格式库。权限演示角色为sales/hr/procurement，employee用于授权变更实验。

catalog.json是显式导入清单，记录源字节SHA-256、租户、文档ID、版本、权限和有效期。不要直接删除源文件模拟删除：使用更高版本的delete项。修改正文或权限后必须提高版本并更新摘要。有效期起日包含、止日不包含；null表示无止日。所有日期是业务日期，不是时区时间戳。

同一固定依赖组合下生成可重复：PDF使用invariant模式，DOCX固定属性及ZIP时间。跨库升级不保证字节一致；依赖版本变化需重新核对并提升文档版本。

支持正文Markdown、文本PDF、DOCX段落和简单表格。不支持OCR、复杂布局、修订、图片、文本框、页眉页脚内容、宏或附件；不应把解析成功等同于语义正确。PDF页号是真实物理页，DOCX/Markdown只有结构块号。字符切块不是token预算。

本地已完成真实格式回读与PDF代表样例视觉检查。DOCX版面渲染因环境缺少LibreOffice不可执行，不能宣称排版验收通过；仅验证结构顺序和文本。本仓库提交生成器及3篇Markdown示例，生成的二进制样例不随Git提交。

可信教学文件的大小限制不构成恶意解析沙箱。生产环境不可信文档应隔离解析工作进程，并配置CPU/内存/超时限制。权限参数只是可信CLI身份演示，不能代替用户认证。

参考：[pypdf文本提取限制](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)、[python-docx正文顺序接口](https://python-docx.readthedocs.io/en/latest/api/document.html)。
