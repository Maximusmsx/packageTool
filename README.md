# 自动计税数据库打包工具

图形化（Tkinter）打包工具：按配置把多个业务目录中「最新日期目录」分别打成一个 rar，再合并成桌面上一个总 rar。

## 1. 直接使用

双击 `dist\自动计税数据库打包工具.exe` 即可，单文件、免安装、运行不联网。

可把 exe 复制到任意目录（如桌面）使用；配置 `config.json` 会保存在 exe 同目录，下次启动自动加载。

### 主界面

- **+添加打包目录**：弹出添加窗口，选择「打包目录」+ 填写「打包名前缀」，点「保存」写入配置。
- 列表支持 **双击编辑**、**选中后按 Delete 删除**、**右键菜单**（编辑 / 删除 / 打开目录）。
- **打包名**：最终压缩包名称，打包时也会存入配置。
- **开始打包**：按配置逐个目录打包，弹出进度窗口显示日志与进度。

### 打包规则

1. 在配置的打包目录下，找形如 `yyyy-mm-dd` 的子目录，取日期最大的那个；
2. 压缩为 `<打包名前缀><日期>.rar`，例如 `TAX_CENTER_BS2026-11-07.rar`（包内保留该日期目录这一层结构）；
3. 全部目录完成后，把这些子包一起压缩到 `桌面\<打包名>.rar`；
4. 中间临时压缩包在 `%TEMP%\autotaxpack_*`，无论成功失败都会自动删除，不留垃圾。

若某个配置目录不存在或没有日期目录，会先提示并询问是否跳过；若桌面已存在同名总包，会询问是否覆盖。

## 2. 配置文件

`config.json`（UTF-8，与 exe 同目录；若该目录不可写则保存到 `%APPDATA%\AutoTaxPackTool\config.json`）：

```json
{
  "items": [
    {"dir": "C:\\Users\\admin\\Desktop\\增值税发票\\XCES_V2\\TAX_CENTER_BS", "prefix": "TAX_CENTER_BS"}
  ],
  "pack_name": "自动计税数据库",
  "geometry": "880x420+120+80",
  "last_browse": "C:\\Users\\admin\\Desktop"
}
```

## 3. 从源码运行 / 重新打包 exe

```bat
python main.py      :: 直接运行源码
python build.py     :: 重新生成 dist\自动计税数据库打包工具.exe
```

- 依赖：Python 3.7+、PyInstaller（打包时用）、本机已安装 WinRAR（构建时会把 `Rar.exe` 内置进 exe）。
- exe 已内置 `Rar.exe`，运行电脑无需安装 WinRAR。

## 4. 文件说明

| 文件 | 说明 |
| --- | --- |
| `main.py` | 全部程序逻辑与界面 |
| `build.py` | 一键打包脚本（PyInstaller，内置 Rar.exe） |
| `app.ico` | 程序图标 |
| `dist\自动计税数据库打包工具.exe` | 生成的单文件程序 |
