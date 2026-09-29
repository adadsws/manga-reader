# Magi 漫画布局模型

## 用途与结构

相对项目根目录的权重路径是 `models/layout/magiv2/pytorch_model.bin`。`magiv2/` 保存漫画分镜与文字区域检测所需的 Magi v2 权重：

```text
models/
└─ layout/
   ├─ magiv2.md
   └─ magiv2/
      └─ pytorch_model.bin
```

当前权重为 2,063,693,064 字节。服务通过 `server/layout.py` 加载它，用于分镜候选、嵌套画格和文字区域分析。

## 来源

固定下载地址、上游说明、许可和 SHA-256 见 [reference/layout/magiv2.md](../../reference/layout/magiv2.md)，机器可读锁定信息见 `config/layout-model.lock.json`。

## 未纳入实际文件的原因

单个权重约 1.92 GiB，远超 100 MiB，不适合进入普通 Git 历史或远程代码仓库。`.gitignore` 只忽略同级实际模型目录，本说明继续由 Git 跟踪。

## 重建方法

使用独立下载脚本 [prepare_layout.py](../../tools/prepare_layout.py)。它读取 [layout-model.lock.json](../../config/layout-model.lock.json) 中的固定 revision、文件大小和 SHA-256，在项目根目录运行：

```powershell
python tools/prepare_layout.py
```

工具按锁文件下载并校验大小与 SHA-256；校验失败时不得使用残缺文件。运行环境仍需在 `config/runtime.json` 和 `config/reader.json` 中指向正确路径。
