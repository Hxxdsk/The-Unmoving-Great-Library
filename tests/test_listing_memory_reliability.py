from suite.listing.workflow import WorkflowStore


def test_failed_recipe_is_quarantined_and_given_to_ai_as_failure_context(tmp_path):
    store = WorkflowStore(tmp_path / "memory.json")
    actions = [{"kind": "target", "target": {"text": "图文信息"}}]
    store.remember(
        "图文信息",
        "fxg.jinritemai.com/ffa/g/edit",
        actions,
        source="AI",
        outcome="editor",
    )
    recipe = store.recipes("图文信息", "fxg.jinritemai.com/ffa/g/edit")[0]
    store.failed(recipe, "点击后没有切换到目标区域")
    restored = WorkflowStore(store.path)
    assert not restored.recipes("图文信息", "fxg.jinritemai.com/ffa/g/edit")
    assert not restored.hints("图文信息", "fxg.jinritemai.com/ffa/g/edit")
    assert (
        "没有切换"
        in restored.avoidances("图文信息", "fxg.jinritemai.com/ffa/g/edit")[0][
            "失败原因"
        ]
    )
    restored.remember(
        "图文信息",
        "fxg.jinritemai.com/ffa/g/edit",
        actions,
        source="AI",
        outcome="verified",
    )
    assert (
        restored.recipes("图文信息", "fxg.jinritemai.com/ffa/g/edit")[0][
            "total_failures"
        ]
        == 1
    )


def test_new_failed_ai_plan_never_enters_successful_recipes(tmp_path):
    store = WorkflowStore(tmp_path / "memory.json")
    actions = [{"kind": "target", "target": {"text": "错误入口"}}]
    store.reject(
        "进入下一步",
        "fxg.jinritemai.com/ffa/g/create",
        actions,
        source="AI",
        reason="未进入编辑页",
    )
    assert store.recipes("进入下一步", "fxg.jinritemai.com/ffa/g/create") == []
    assert store.avoidances("进入下一步", "fxg.jinritemai.com/ffa/g/create")
    assert not store.avoidances("保存草稿", "fxg.jinritemai.com/ffa/g/create")


def test_listing_window_has_no_human_guidance_controls(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QCoreApplication, QEvent
    from suite.listing.ui import ListingWindow, QStandardPaths

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda *_: str(tmp_path))
    window = ListingWindow()
    assert not hasattr(window, "manual_enabled")
    assert not hasattr(window, "manual_panel")
    window.preferences["manual_guidance"] = "on"
    window.save_preferences()
    assert "manual_guidance" not in window.config_path.read_text()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_legacy_unverified_recipe_is_revalidated_before_replay(tmp_path):
    store = WorkflowStore(tmp_path / "memory.json")
    actions = [{"kind": "target", "target": {"text": "下一步"}}]
    page = "fxg.jinritemai.com/ffa/g/create"
    store.remember("下一步", page, actions)
    recipe = store.recipes("下一步", page)[0]
    recipe.pop("verified")
    store.save()
    restored = WorkflowStore(store.path)
    assert not restored.recipes("下一步", page)
    restored.remember("下一步", page, actions, source="AI", outcome="editor")
    assert restored.recipes("下一步", page)[0]["verified"]
