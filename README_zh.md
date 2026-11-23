# 本地多模态文件检索

本项目提供本地文件索引、命令行和浏览器语义搜索。仅扫描明确配置的目录；推理服务和模型需要单独部署。搜索结果是相似文件及文本片段，不是生成式回答或经过校准的可信度。

真实界面截图与可复现的示例演示见 [Working demonstration](README.md#working-demonstration)。演示仅使用仓库内五份虚构文档，不包含私有文件或凭据。

请按 [英文安装说明](README.md) 使用校验固定版本的 Python 运行时，安装依赖，复制 `config.ollama.example.yaml`，配置 `scanner.scan_paths` 和数据库路径，再依次执行 `init-db`、`scan`、`extract-meta`、`process`、`search` 和 `serve`。

浏览器默认地址为 `http://127.0.0.1:8100/`，设置页为 `/config`。使用数据库旁的私有 `.api_token` 登录。请勿提交真实凭据、工作配置、索引或备份。

OCR、音频、视觉描述和旧格式转换是可选部署。Jina 固定后端必须保留应用与服务的单输入、单并发限制。参见 [服务说明](docs/SERVICE_SETUP.md)、[模型说明](MODEL_CARD.md) 和 [已知限制](docs/KNOWN_LIMITATIONS.md)。

这是本地工程试用项目，不代表生产质量认证。保留 [项目许可](LICENSE) 与 [第三方声明](THIRD_PARTY_NOTICES.md)。
