# 第三方组件与许可证

本程序（Cyrene Keyboard）以 **GNU Affero General Public License v3.0** 发布，全文见 [LICENSE](LICENSE)。

为了方便别人免安装使用，发布的分发包里**捆绑了下列第三方组件**。它们各自的许可证如下，
与本项目的 AGPL-3.0 均兼容。分发包的 `licenses/` 目录里随附了这些许可证的原文副本。

## 随分发包一起提供的组件

| 组件 | 版本 | 许可证 | 上游 |
|---|---|---|---|
| PySide6 (Essentials / Addons) | 6.12.0 | **LGPL-3.0-only**（另可选 GPL-2.0 / GPL-3.0） | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| shiboken6 | 6.12.0 | 同上 | 同上 |
| Qt 6 | 6.12.0 | LGPL-3.0 / GPL-3.0（随 PySide6 分发） | https://www.qt.io/ |
| numpy | 2.5.3 | BSD-3-Clause（另含 0BSD / MIT / Zlib / CC0-1.0 片段） | https://numpy.org/ |
| soundcard | 0.4.6 | BSD-3-Clause | https://github.com/bastibe/SoundCard |
| hidapi | 0.15.0 | BSD-3-Clause **或** GPL-3.0（双许可，本项目按 BSD-3-Clause 使用） | https://github.com/trezor/cython-hidapi |
| cffi | 2.1.1 | MIT-0 | https://github.com/python-cffi/cffi |
| pycparser | 3.0 | BSD-3-Clause | https://github.com/eliben/pycparser |
| Python（嵌入式运行时，仅便携版） | 3.13.12 | PSF License Agreement | https://www.python.org/ |

## 关于 LGPL 组件（PySide6 / Qt / shiboken6）的说明

LGPL-3.0 允许把库随闭源或其它许可证的程序一起分发，但要求满足以下条件。本项目按如下方式满足：

1. **随附许可证副本** —— 分发包内 `licenses/` 目录含 LGPL-3.0 与 GPL-3.0 全文，
   以及各组件自己的 `dist-info/licenses/` 原始文件。
2. **声明使用了这些库** —— 即本文件的表格。
3. **允许用户替换该库** —— PySide6 与 Qt 以**独立的 DLL 文件**形式存放，没有被静态链接进本程序的
   可执行文件。用户可以直接替换 `PySide6/` 目录下的 DLL 为自己编译的版本，本程序无需重新构建即可使用。
4. **提供对应源码** —— PySide6/Qt 源码可从上游（上表链接）获取，本项目未修改它们。

本项目没有修改 Qt / PySide6 / shiboken6 / numpy / soundcard / hidapi / cffi / pycparser 的任何源码。

## 关于「Python 解释器有签名」的说明

便携版里用的是 Python 官方发布的、由 Python Software Foundation **数字签名**的 `python.exe` / `pythonw.exe`。
这只是说明**那个解释器文件**来源可信，**不代表本程序（Cyrene Keyboard）本身经过代码签名或安全认证**。
本程序自己的代码是明文 `.py` 脚本，没有签名。详见 README 的「Windows 安全提示」一节。

## 不随分发包提供的东西

- 键盘厂商的固件升级工具、固件包、说明书照片：版权归原厂或拍摄者，不在本仓库与分发包内（见 `.gitignore`）。
- 本项目的开发日志与内部审查记录：含硬件标识与协议逆向细节，仅本地留档。

## 本项目自己的素材（重要：图标不在 AGPL 范围内）

- `assets/icon.ico`、`assets/icon.png`、`assets/icon_256.png`：本项目绘制的**同人图标**。
  角色「昔涟」及《崩坏：星穹铁道》相关形象的版权归 **miHoYo（米哈游）** 所有。
  这些图标**不适用 AGPL-3.0 的授权**，仅随本程序用于个人非商业场景。
  若要再分发或商业使用，请自行取得相应授权。
- `docs/` 下的截图是本程序运行界面的截图，属于本项目作品，随 AGPL-3.0 提供。

也就是说：**代码是 AGPL-3.0 的，图标不是。** 两者的授权范围不同，请勿混用。
