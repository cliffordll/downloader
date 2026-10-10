"""Regenerate file icons with centered circles and separated characters.

Run with Python and Pillow; --font can select a font file on another OS.
"""

import argparse
import json
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1] / 'icons'
SPACING = 1
FONT_SIZE = 8.5
GAP_PADDING = 2
CIRCLE = (4.5, 4.5, 19.5, 19.5)
TEXT_CENTER = (CIRCLE[0] + CIRCLE[2]) / 2
TEMPLATES = Path(__file__).resolve().parent / 'icon_templates'
MASTER_SCALE = 16
SHORT_LABELS = {'MPEG-1': 'MPG1', 'MPEG1': 'MPG1',
                'MPEG-2': 'MPG2', 'MPEG2': 'MPG2',
                'MPEG-4': 'MPG4', 'MPEG4': 'MPG4', 'SQLITE': 'SQL'}


def save_png(image, path):
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.png', delete=False) as output:
        temporary = Path(output.name)
        image.save(output, format='PNG')
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def glyph(character, font):
    mask = Image.new('L', (font.size * 2, font.size * 2))
    draw = ImageDraw.Draw(mask)
    draw.text((0, 0), character, font=font, fill=255)
    return mask.crop(mask.getbbox())


def render_frame(size):
    """文件与新建图标共用 1.35px 粗线、圆角和折角，按目标尺寸绘制。"""
    unit = max(MASTER_SCALE, size / 24 * 4)
    frame = Image.new('RGBA', (round(24 * unit), round(24 * unit)))
    draw = ImageDraw.Draw(frame)

    def coords(values):
        return tuple(round(value * unit) for value in values)

    draw.rounded_rectangle(coords((4.075, 2.125, 19.925, 21.875)),
                           radius=round(1.1 * unit), fill='#444444')
    draw.rounded_rectangle(coords((5.425, 3.475, 18.575, 20.525)),
                           radius=round(0.35 * unit), fill=(0, 0, 0, 0))
    draw.polygon([coords(p) for p in ((14.55, 2.125), (24, 2.125), (24, 7.5), (19.925, 7.5))],
                 fill=(0, 0, 0, 0))
    draw.line([coords(p) for p in ((14.1, 2.6), (19.45, 7.95))],
              fill='#444444', width=round(1.35 * unit))
    draw.line([coords(p) for p in ((13.675, 2.8), (13.675, 7.675), (19.25, 7.675))],
              fill='#444444', width=round(1.35 * unit), joint='curve')
    return frame.resize((size, size), Image.Resampling.LANCZOS)


def render(original, label, colour, font_path, text_colour='#444444', frame_template=None,
           text_offset_y=1.5, font_axes=None, spacing=SPACING, font_size=FONT_SIZE,
           circle_offset_y=0):
    size = original.height
    scale = size / 24
    requested_spacing = spacing
    # 画布两侧各留 1 DIP；先缩字距，再按整行缩字号，绝不横向拉伸字符。
    available = 22 * MASTER_SCALE
    while True:
        font = ImageFont.truetype(str(font_path), round(font_size * MASTER_SCALE))
        if font_axes is not None:
            font.set_variation_by_axes(font_axes)
        cap_height = glyph('M', font).height
        masks = [glyph(c, font) for c in label]
        if label == '+':
            mask = masks[0]
            # 加号交叉处视觉偏重，线宽用 19/16px；保留外接尺寸和中心位置。
            width = round(mask.width * cap_height / mask.height)
            mask = Image.new('L', (width, cap_height))
            stroke = round(19 / 16 * MASTER_SCALE)
            x = (width - stroke) // 2
            y = (cap_height - stroke) // 2
            mask.paste(255, (x, 0, x + stroke, cap_height))
            mask.paste(255, (0, y, width, y + stroke))
            masks = [mask]
        spacing_units = round(requested_spacing * MASTER_SCALE)
        if len(masks) > 1:
            fit_spacing = (available - sum(mask.width for mask in masks)) // (len(masks) - 1)
            spacing_units = min(spacing_units, max(round(0.25 * MASTER_SCALE), fit_spacing))
        width_units = sum(mask.width for mask in masks) + spacing_units * (len(masks) - 1)
        if width_units <= available:
            break
        font_size -= 0.25
        if font_size < 5:
            raise ValueError(f'Label too long; provide an abbreviation: {label}')
    spacing = spacing_units / MASTER_SCALE
    width = width_units / MASTER_SCALE
    left = TEXT_CENTER - width / 2
    right = left + width
    gaps = left <= 5.5 or right >= 18.5
    baseline = 16 + text_offset_y
    band_top = baseline - cap_height / MASTER_SCALE - GAP_PADDING
    band_bottom = baseline + GAP_PADDING if gaps else baseline + 1

    background = Image.new('RGBA', (24 * MASTER_SCALE, 24 * MASTER_SCALE))
    draw = ImageDraw.Draw(background)
    unit = MASTER_SCALE
    if colour is not None:
        circle = (CIRCLE[0], CIRCLE[1] + circle_offset_y,
                  CIRCLE[2], CIRCLE[3] + circle_offset_y)
        draw.ellipse(tuple(round(v * unit) for v in circle), fill=colour)
    background = background.resize((size, size), Image.Resampling.LANCZOS)
    if frame_template is None or Path(frame_template).resolve() == (TEMPLATES / 'file-square.png').resolve():
        frame = render_frame(size)
    else:
        with Image.open(frame_template) as template:
            frame = template.convert('RGBA').resize((size, size), Image.Resampling.LANCZOS)
    if gaps:
        band = (0, round(band_top * scale), size, round(band_bottom * scale))
        frame.paste((0, 0, 0, 0), band)
    body = Image.alpha_composite(background, frame)
    result = body
    text = Image.new('L', (24 * MASTER_SCALE, 24 * MASTER_SCALE))
    cursor = (text.width - width_units) // 2
    for mask in masks:
        top = round(baseline * MASTER_SCALE) - cap_height + (cap_height - mask.height) // 2
        text.paste(mask, (cursor, top))
        cursor += mask.width + round(spacing * MASTER_SCALE)
    text = text.resize(result.size, Image.Resampling.LANCZOS)
    result.paste(text_colour, (0, 0), text)
    return result, dict(label_left=left, label_right=right,
                        frame_gaps=gaps, character_gap=spacing,
                        condensed=False, label_center=TEXT_CENTER,
                        font_size=font_size, cap_height=cap_height / MASTER_SCALE,
                        baseline=baseline, text_offset_y=text_offset_y, display_label=label,
                        font_family=font.getname()[0], font_style=font.getname()[1],
                        font_axes=font_axes,
                        native_pixel_text=False, rendering='supersampled-label',
                        pixel_aligned_label=False,
                        text_colour=text_colour,
                        canvas_padding=0,
                        frame_gap_padding=GAP_PADDING if gaps else 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', type=Path, default=Path('C:/Windows/Fonts/seguisb.ttf'))
    parser.add_argument('--font-axes', type=float, nargs='+', help='Variable-font axes in font order (Bahnschrift: weight width)')
    parser.add_argument('--text-colour', help='Override label colour for the selected icons')
    parser.add_argument('--frame-template', type=Path, help='Use a transparent frame PNG for selected icons')
    parser.add_argument('--text-offset-y', type=float, help='Vertical label offset in 24px design units')
    parser.add_argument('--spacing', type=float, help='Visible character gap in 24px design units')
    parser.add_argument('--font-size', type=float, help='Font size in 24px design units')
    parser.add_argument('--circle-offset-y', type=float, help='Circle vertical offset in 24px design units')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--names', nargs='+', help='Only regenerate the named file icons')
    selection.add_argument('--all', action='store_true', help='Explicitly regenerate all file icons')
    args = parser.parse_args()
    if not args.font.is_file():
        parser.error('Font file missing; supply --font PATH')
    if not args.all and not args.names:
        args.names = ['m3u8', 'ts', 'mp4']
    manifest_path = ROOT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (args.all or args.names and 'new' in args.names) and not any(
            entry['category'] == 'files' and entry['name'] == 'new' for entry in manifest['icons']):
        manifest['icons'].append(dict(category='files', name='new', label='+',
                                     origin='new-file', added=True, source='source/files/new.png',
                                     circle_color=None))
    entries = [i for i in manifest['icons'] if i['category'] == 'files']
    if args.names:
        unknown = set(args.names) - {i['name'] for i in entries}
        if unknown:
            parser.error(f'Unknown file icons: {sorted(unknown)}')
        entries = [i for i in entries if i['name'] in args.names]
    for entry in entries:
        name = entry['name']
        entry['display_label'] = SHORT_LABELS.get(entry['label'], entry['label'])
        if args.frame_template:
            entry['frame_template'] = args.frame_template.resolve().relative_to(ROOT.parent).as_posix()
        if args.text_offset_y is not None:
            entry['text_offset_y'] = args.text_offset_y
        if args.spacing is not None:
            entry['character_gap'] = args.spacing
        if args.font_size is not None:
            entry['font_size'] = args.font_size
        if args.circle_offset_y is not None:
            entry['circle_offset_y'] = args.circle_offset_y
        frame_template = ROOT.parent / entry['frame_template'] if entry.get('frame_template') else None
        circle_offset_y = entry.get('circle_offset_y', 0)
        entry['circle_bounds'] = [CIRCLE[0], CIRCLE[1] + circle_offset_y,
                                  CIRCLE[2], CIRCLE[3] + circle_offset_y]
        for directory in ('source', *(str(s) for s in manifest['sizes'])):
            path = ROOT / directory / 'files' / f'{name}.png'
            path.parent.mkdir(parents=True, exist_ok=True)
            with Image.new('RGBA', (384, 384) if directory == 'source' else (int(directory), int(directory))) as original:
                result, metrics = render(original, entry['display_label'],
                                         entry['circle_color'], args.font,
                                         args.text_colour or manifest['file_frame']['text_metrics']
                                         .get(name, {}).get('text_colour', '#444444'), frame_template,
                                         entry.get('text_offset_y', 1.5), args.font_axes,
                                         entry.get('character_gap', SPACING), entry.get('font_size', FONT_SIZE),
                                         circle_offset_y)
            save_png(result, path)
            if name == 'new':
                tool_path = ROOT / directory / 'tools/new.png'
                tool_path.parent.mkdir(parents=True, exist_ok=True)
                save_png(result, tool_path)
            if directory == '24':
                base_metrics = metrics
        manifest['file_frame']['text_metrics'].setdefault(name, {}).update(base_metrics)
        print(name, base_metrics)
    if any(entry['name'] == 'new' for entry in entries) and not any(
            entry['category'] == 'tools' and entry['name'] == 'new' for entry in manifest['icons']):
        manifest['icons'].append(dict(category='tools', name='new', label='新建文件',
                                     origin='synced:files/new.png', added=True,
                                     source='source/tools/new.png'))
    if not args.names:
        manifest['file_frame']['circle'].update(x=CIRCLE[0], y=CIRCLE[1])
        if args.circle_offset_y is not None:
            manifest['file_frame']['circle']['y'] += args.circle_offset_y
        manifest['file_frame']['label_rule'] = (
            f'{base_metrics["font_family"]} {base_metrics["font_style"]}, '
            '8.5px font, default 1px character gaps at 24px, reduce gaps only to fit. '
            'Labels and circles share center x=12; baseline y=17.5. '
            'Long file types use short display labels. Fixed square canvas; '
            'no individual letter is compressed. Plus sign matches cap height. '
            'Frame gaps add 2px padding.'
        )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
