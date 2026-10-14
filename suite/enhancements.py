"""在原稿之上应用各工具的独立扩展；原始脚本保留。"""


def enhance(module, key):
    if getattr(module, "_suite_enhanced", False):
        return
    if key == "generator":
        from .generator_adapter import apply
    elif key == "matcher":
        from .matcher_adapter import apply
    elif key == "joiner":
        from .joiner_adapter import apply
    else:
        return
    apply(module)
    module._suite_enhanced = True
