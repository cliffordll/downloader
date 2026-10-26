import json
from pathlib import Path
import re
import unittest
from xml.etree import ElementTree



class FileIconManifestTests(unittest.TestCase):
    def test_file_icons_inherit_shared_style_defaults(self):
        manifest_path = Path(__file__).resolve().parents[1] / 'icons' / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        defaults = manifest['file_frame']['style_defaults']
        icons = [icon for icon in manifest['icons'] if icon['category'] == 'files']

        self.assertEqual(defaults['font_family'], 'Segoe UI')
        self.assertEqual(defaults['font_style'], 'Semibold')
        self.assertEqual(defaults['font_size'], 8.5)
        self.assertEqual(defaults['character_gap'], 1.0)
        self.assertEqual(defaults['text_offset_y'], 1.5)
        self.assertEqual(defaults['circle_offset_y'], 1.5)
        for icon in icons:
            for key in ('frame_template', 'text_offset_y', 'character_gap', 'font_size',
                        'circle_offset_y', 'circle_bounds'):
                self.assertNotIn(key, icon, f"{icon['name']} duplicates shared style key {key}")

    def test_tuned_open_and_key_icons_are_registered_without_removed_key_variants(self):
        icon_root = Path(__file__).resolve().parents[1] / 'icons'
        manifest = json.loads((icon_root / 'manifest.json').read_text(encoding='utf-8'))
        tools = {icon['name']: icon for icon in manifest['icons'] if icon['category'] == 'tools'}

        self.assertEqual(tools['open']['source'], 'source/tools/open.png')
        self.assertEqual(tools['key']['vector_source'], 'source/tools/key.svg')
        self.assertTrue((icon_root / tools['open']['source']).is_file())
        self.assertTrue((icon_root / tools['key']['source']).is_file())
        self.assertNotIn('key-classic', tools)
        self.assertNotIn('key-horizontal', tools)

    def test_download_icons_share_the_same_arrow_geometry_and_tray(self):
        icon_root = Path(__file__).resolve().parents[1] / 'icons'
        manifest = json.loads((icon_root / 'manifest.json').read_text(encoding='utf-8'))
        names = ('download-all', 'task-download', 'task-pause', 'task-resume', 'task-stop')
        tools = {icon['name']: icon for icon in manifest['icons']
                 if icon['category'] == 'tools' and icon['name'] in names}
        roots = {}
        for name in names:
            vector_source = tools[name]['vector_source']
            self.assertTrue((icon_root / vector_source).is_file())
            roots[name] = ElementTree.parse(icon_root / vector_source).getroot()
        start = roots['task-download']
        download_all = roots['download-all']
        start_group = start.find('{http://www.w3.org/2000/svg}g')
        all_group = download_all.find('{http://www.w3.org/2000/svg}g')
        start_paths = [path.attrib['d'] for path in start_group]
        all_paths = [path.attrib['d'] for path in all_group]

        self.assertEqual(start_group.attrib, all_group.attrib)
        self.assertEqual(len(start_paths), 2)
        self.assertEqual(len(all_paths), 3)
        self.assertEqual(start_paths[0], 'M12 4.048v8.904m-3.816-3.816 3.816 3.816 3.816-3.816')
        self.assertEqual(all_paths[0], 'M8 4.048v8.904m-3.816-3.816L8 12.952l3.816-3.816')
        self.assertEqual(all_paths[1], 'M16 4.048v8.904m-3.816-3.816 3.816 3.816 3.816-3.816')
        self.assertEqual(all_paths[2], start_paths[1])
        for name in names[2:]:
            group = roots[name].find('{http://www.w3.org/2000/svg}g')
            self.assertEqual(group.attrib, start_group.attrib)
            self.assertEqual(list(group)[-1].attrib['d'], start_paths[-1])

    def test_task_start_and_resume_have_distinct_action_symbols(self):
        icon_root = Path(__file__).resolve().parents[1] / 'icons'
        manifest = json.loads((icon_root / 'manifest.json').read_text(encoding='utf-8'))
        tools = {icon['name']: icon for icon in manifest['icons'] if icon['category'] == 'tools'}
        self.assertEqual(tools['task-download']['label'], '下载任务')
        self.assertEqual(tools['task-start']['label'], '开始任务')
        self.assertEqual(tools['task-resume']['label'], '继续下载任务')
        symbols = {}
        for name in ('task-start', 'task-resume'):
            root = ElementTree.parse(icon_root / tools[name]['vector_source']).getroot()
            group = root.find('{http://www.w3.org/2000/svg}g')
            symbols[name] = list(group)[0].attrib['d']
            self.assertEqual(list(group)[-1].attrib['d'],
                             'M2.9 15.3v3.85q0 .6.6.6h17q.6 0 .6-.6V15.3')
        self.assertEqual(symbols['task-start'], 'M7.548 4.042v8.916l8.904-4.458z')
        self.assertNotEqual(symbols['task-start'], symbols['task-resume'])

    def test_task_resume_reuses_retry_shape_with_aligned_size_and_larger_opening(self):
        icon_root = Path(__file__).resolve().parents[1] / 'icons'
        manifest = json.loads((icon_root / 'manifest.json').read_text(encoding='utf-8'))
        tools = {icon['name']: icon for icon in manifest['icons'] if icon['category'] == 'tools'}
        resume_root = ElementTree.parse(icon_root / tools['task-resume']['vector_source']).getroot()
        resume_group = resume_root.find('{http://www.w3.org/2000/svg}g')
        resume_items = list(resume_group)
        ring = resume_items[0]
        arrow = resume_items[1]
        tray = resume_items[2]
        self.assertEqual(ring.attrib['d'],
                         'M317.1 242.1 A135 135 0 1 1 324.949 168.057')
        self.assertEqual(arrow.attrib['d'], 'M339 109 L339 169 L280 169')
        self.assertEqual(ring.attrib['transform'],
                         'translate(5.2044672 1.7224448) scale(.0353934)')
        self.assertEqual(arrow.attrib['transform'],
                         'translate(5.2044672 1.7224448) scale(.0353934)')
        self.assertEqual(ring.attrib['stroke-width'], '38.142704572')
        self.assertEqual(arrow.attrib['stroke-width'], '38.142704572')
        self.assertEqual(tray.attrib['d'],
                         'M2.9 15.3v3.85q0 .6.6.6h17q.6 0 .6-.6V15.3')

    def test_upload_icons_use_matching_upward_arrow_and_shared_tray(self):
        icon_root = Path(__file__).resolve().parents[1] / 'icons'
        manifest = json.loads((icon_root / 'manifest.json').read_text(encoding='utf-8'))
        names = ('task-upload', 'upload-all')
        tools = {icon['name']: icon for icon in manifest['icons']
                 if icon['category'] == 'tools' and icon['name'] in names}
        roots = {}
        for name in names:
            vector_source = tools[name]['vector_source']
            self.assertTrue((icon_root / vector_source).is_file())
            roots[name] = ElementTree.parse(icon_root / vector_source).getroot()
            self.assertTrue((icon_root / tools[name]['source']).is_file())
            for size in (24, 30, 36, 48):
                self.assertTrue((icon_root / str(size) / 'tools' / f'{name}.png').is_file())
        single = list(roots['task-upload'].find('{http://www.w3.org/2000/svg}g'))
        double = list(roots['upload-all'].find('{http://www.w3.org/2000/svg}g'))
        self.assertEqual(len(single), 2)
        self.assertEqual(len(double), 3)
        self.assertEqual(single[-1].attrib['d'], double[-1].attrib['d'])
        self.assertEqual(single[0].attrib['d'], 'M12 12.952V4.048m-3.816 3.816L12 4.048l3.816 3.816')
        self.assertEqual(double[0].attrib['d'], 'M8 12.952V4.048m-3.816 3.816L8 4.048l3.816 3.816')
        self.assertEqual(double[1].attrib['d'], 'M16 12.952V4.048m-3.816 3.816L16 4.048l3.816 3.816')


if __name__ == '__main__':
    unittest.main()
