"""仅验证加载队列与显式排序，不调用真实 AI。"""
import gc
import threading
import time
from unittest.mock import patch

from PIL import Image
import tkinter as tk
from tkinter import ttk

from suite.legacy import load_original, common_ttk_theme


def pump(root, condition):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        root.update()
        if condition():
            return
        time.sleep(.01)
    raise AssertionError('加载队列没有按时处理完')


def exercise(tmp_path, remove_first=False):
    module = load_original('joiner')
    root = tk.Tk(); root.withdraw()
    app = module.ImageJoinerApp(root)
    common_ttk_theme(root)
    app.ai_panel.enabled.set(False)
    batches = []
    for i, name in enumerate(('A', 'B', 'C')):
        folder = tmp_path / name; folder.mkdir()
        files = []
        for number in (2, 1):
            path = folder / f'{number}.png'
            Image.new('RGB', (50, 70), ('black', 'blue', 'pink')[i]).save(path)
            files.append((str(path), i))
        batches.append(files)
    started, release = threading.Event(), threading.Event()
    reads = []
    original = module.open_rgba
    def read(path):
        reads.append(path)
        if len(reads) == 1:
            started.set()
            assert release.wait(3)
        return original(path)
    try:
        with patch.object(module, 'open_rgba', side_effect=read), patch.object(app, 'auto_group_sort', wraps=app.auto_group_sort) as sorter:
            app._start_load(batches[0])
            assert started.wait(1)
            if remove_first:
                app._start_load(batches[1])
                expected = batches[1]
            else:
                app._start_load(batches[0] + batches[1])
                app._start_load(batches[0] + batches[1] + batches[2])
                expected = sum(batches, [])
            release.set()
            pump(root, lambda: not app.task.busy and app._pending_sources is None and len(app.image_list) == len(expected))
            assert [item['path'] for item in app.image_list] == [p for p, _ in expected]
            assert len(reads) == (4 if remove_first else 6)
            sorter.assert_not_called()
            before = [item['path'] for item in app.image_list]
            app._toggle_side_badge(0)
            assert [item['path'] for item in app.image_list] == before
            sorter.assert_not_called()
            app.auto_group_sort(show_message=False)
            sorter.assert_called_once()
        assert 'SuiteCheck.indicator' in str(ttk.Style(root).layout('TCheckbutton'))
        assert 'SuiteCheck.indicator' in str(ttk.Style(root).layout('Modern.TCheckbutton'))
    finally:
        release.set()
        root.destroy()
        del app
        gc.collect()


def test_folders_added_while_loading_append_once_without_auto_sort(tmp_path):
    exercise(tmp_path)


def test_removed_folder_is_not_restored_by_busy_load(tmp_path):
    exercise(tmp_path, remove_first=True)


def test_ai_runs_only_after_smart_sort_and_pairs_colors(tmp_path):
    from color_tool.gemini import GeminiClient
    module = load_original('joiner')
    root = tk.Tk(); root.withdraw()
    paths = []
    try:
        with patch('suite.ai_controls.shared_client') as factory:
            app = module.ImageJoinerApp(root)
            client = GeminiClient('test-key', 'models/gemini-test', app.task.cancel)
            factory.return_value = client
            for i, color in enumerate(('black', 'blue', 'black', 'blue')):
                folder = tmp_path / str(i); folder.mkdir()
                path = folder / 'picture.png'
                Image.new('RGB', (50, 70), color).save(path)
                paths.append((str(path), i))
            app._start_load(paths)
            pump(root, lambda: not app.task.busy and len(app.image_list) == 4)
            factory.assert_not_called()
            assert [item['path'] for item in app.image_list] == [p for p, _ in paths]
            rows = []
            for i, (side, color, group) in enumerate((('背面','黑色','black'), ('正面','蓝色','blue'), ('正面','黑色','black'), ('背面','蓝色','blue'))):
                rows.append({'id': str(i), 'side': side, 'color_label': color, 'pair_id': group,
                    'garment_kind': '外套', 'confidence': .98, 'needs_review': False, 'reason': '主体同色，门襟与后背结构明确'})
            with patch.object(client, 'generate', return_value={'images': rows}) as generate:
                app.auto_group_sort()
                pump(root, lambda: not app.task.busy)
                generate.assert_called_once()
            factory.assert_called_once()
            assert [item['path'] for item in app.image_list] == [paths[i][0] for i in (2,0,1,3)]
            assert [item['side'] for item in app.image_list] == ['正面','背面','正面','背面']
            assert [item['group_id'] for item in app.image_list] == [1,1,2,2]
            assert not any(item['soft_pair'] for item in app.image_list)
    finally:
        root.destroy()
        del app
        gc.collect()


def test_invalid_ai_pairing_preserves_input_items(tmp_path):
    import pytest
    from color_tool.gemini import GeminiClient, GeminiError
    from suite.joiner_adapter import recognize_pairs
    items, rows = [], []
    for i in range(3):
        path = tmp_path / f'{i}.png'
        Image.new('RGB', (20,30), 'black').save(path)
        items.append({'path': str(path), 'side': '待确认'})
        rows.append({'id': str(i), 'side': '正面', 'color_label': '黑色', 'pair_id': 'one',
            'garment_kind': '上衣', 'confidence': .9, 'needs_review': False, 'reason': '可见领口'})
    client = GeminiClient('test-key', 'models/gemini-test')
    with patch.object(client, 'generate', return_value={'images': rows}):
        with pytest.raises(GeminiError, match='超过两张'):
            recognize_pairs(client, items)
    assert all(item == {'path': str(tmp_path / f'{i}.png'), 'side': '待确认'} for i, item in enumerate(items))
