"""pytest 配置：将插件目录与 N.E.K.O 源码根加入 sys.path，使测试可直接导入模块。"""
import importlib.util
import os
import sys

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

_CANONICAL = "steam_manager"

# 从插件目录向上找 N.E.K.O 源码根（含 plugin 包），提供 plugin.plugins.steam_manager
# 宿主入口。本地与 CI（workflow 将插件 mount 进 N.E.K.O 树）均适用。
_cand = _PLUGIN_DIR
while True:
    parent = os.path.dirname(_cand)
    if parent == _cand:
        break
    if os.path.isdir(os.path.join(_cand, "plugin")) and os.path.isfile(
            os.path.join(_cand, "plugin", "__init__.py")):
        if _cand not in sys.path:
            sys.path.insert(0, _cand)
        break
    _cand = parent

# 本地克隆目录名可能不是 steam_manager（如 steam_manager_review）。此时把本地源码
# 注册成 plugin.plugins.steam_manager，避免误测到 N.E.K.O plugin/plugins 下的旧副本。
if os.path.basename(_PLUGIN_DIR) != _CANONICAL:
    root = os.environ.get("NEKO_SOURCE_ROOT", "").strip()
    if root and os.path.isfile(os.path.join(root, "plugin", "__init__.py")):
        if root not in sys.path:
            sys.path.insert(0, root)
    try:
        import plugin.plugins  # noqa: F401

        _name = f"plugin.plugins.{_CANONICAL}"
        _spec = importlib.util.spec_from_file_location(
            _name,
            os.path.join(_PLUGIN_DIR, "__init__.py"),
            submodule_search_locations=[_PLUGIN_DIR],
        )
        if _spec and _spec.loader:
            _module = importlib.util.module_from_spec(_spec)
            sys.modules[_name] = _module
            _spec.loader.exec_module(_module)
            setattr(plugin.plugins, _CANONICAL, _module)
    except Exception as exc:  # 兜底：宿主源码不可用时仍允许纯模块测试导入
        print(f"[conftest] 无法注册本地插件包：{exc}", file=sys.stderr)
