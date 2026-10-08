"""Regenerate file icons with centered circles and separated characters.

Run with Python and Pillow; --font can select a font file on another OS.
"""

import argparse
import json
import math
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1] / 'icons'
SPACING = 1
FONT_SIZE = 9
GAP_PADDING = 2
CIRCLE = (5, 4.5, 20, 19.5)
TEXT_CENTER = (CIRCLE[0] + CIRCLE[2]) / 2
TEMPLATES = Path(__file__).resolve().parent / 'icon_templates'
MASTER_SCALE = 16


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


def render(original, label, colour, font_path, text_colour='#222222'):
    size = original.height
    scale = size / 24
    font = ImageFont.truetype(str(font_path), FONT_SIZE * MASTER_SCALE)
    masks = [glyph(c, font) for c in label]
    width = sum(mask.width for mask in masks) / MASTER_SCALE + SPACING * (len(label) - 1)
    left = TEXT_CENTER - width / 2
    right = left + width
    logical_padding = math.ceil(max(0, -left, right - 24))
    padding = round(logical_padding * scale)
    gaps = left <= 5.1875 or right >= 19.75
    cap_height = glyph('M', font).height
    band_top = 16 - cap_height / MASTER_SCALE - GAP_PADDING
    band_bottom = 16 + GAP_PADDING if gaps else 17

    background = Image.new('RGBA', (24 * MASTER_SCALE, 24 * MASTER_SCALE))
    draw = ImageDraw.Draw(background)
    unit = MASTER_SCALE
    draw.ellipse(tuple(round(v * unit) for v in CIRCLE), fill=colour)
    background = background.resize((size, size), Image.Resampling.LANCZOS)
    with Image.open(TEMPLATES / f'{size}.png') as template:
        frame = template.convert('RGBA')
    if gaps:
        band = (0, round(band_top * scale), size, round(band_bottom * scale))
        frame.paste((0, 0, 0, 0), band)
    body = Image.alpha_composite(background, frame)
    result = Image.new('RGBA', (size + 2 * padding, size))
    result.paste(body, (padding, 0))
    text = Image.new('L', ((24 + 2 * logical_padding) * MASTER_SCALE, 24 * MASTER_SCALE))
    target_left = round((left + logical_padding) * scale)
    cursor = round(target_left * text.width / result.width)
    for mask in masks:
        top = 16 * MASTER_SCALE - cap_height + (cap_height - mask.height) // 2
        text.paste(mask, (cursor, top))
        cursor += mask.width + SPACING * MASTER_SCALE
    text = text.resize(result.size, Image.Resampling.LANCZOS)
    result.paste(text_colour, (0, 0), text)
    return result, dict(label_left=left, label_right=right,
                        frame_gaps=gaps, character_gap=SPACING,
                        condensed=False, label_center=TEXT_CENTER,
                        font_size=FONT_SIZE, cap_height=cap_height / MASTER_SCALE,
                        font_family=font.getname()[0], font_style=font.getname()[1],
                        native_pixel_text=False, rendering='supersampled-label',
                        pixel_aligned_label=True,
                        text_colour=text_colour,
                        canvas_padding=padding / scale,
                        frame_gap_padding=GAP_PADDING if gaps else 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', type=Path, default=Path('C:/Windows/Fonts/seguisb.ttf'))
    parser.add_argument('--text-colour', help='Override label colour for the selected icons')
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
    entries = [i for i in manifest['icons'] if i['category'] == 'files']
    if args.names:
        unknown = set(args.names) - {i['name'] for i in entries}
        if unknown:
            parser.error(f'Unknown file icons: {sorted(unknown)}')
        entries = [i for i in entries if i['name'] in args.names]
    for entry in entries:
        name = entry['name']
        entry['circle_bounds'] = list(CIRCLE)
        for directory in ('source', *(str(s) for s in manifest['sizes'])):
            path = ROOT / directory / 'files' / f'{name}.png'
            with Image.open(path) as original:
                result, metrics = render(original.convert('RGBA'), entry['label'],
                                         entry['circle_color'], args.font,
                                         args.text_colour or manifest['file_frame']['text_metrics']
                                         .get(name, {}).get('text_colour', '#222222'))
            save_png(result, path)
            if directory == '24':
                base_metrics = metrics
        manifest['file_frame']['text_metrics'].setdefault(name, {}).update(base_metrics)
        print(name, base_metrics)
    if not args.names:
        manifest['file_frame']['circle'].update(x=CIRCLE[0], y=CIRCLE[1])
        manifest['file_frame']['label_rule'] = (
            f'{base_metrics["font_family"]} {base_metrics["font_style"]}, '
            '1px visible character gaps at 24px; labels and circles '
            'share center x=12.5. Whole labels use uniform 16x supersampling; '
            'no individual glyph is resized. Long labels extend '
            'the canvas horizontally. Frame gaps add 2px padding.'
        )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
