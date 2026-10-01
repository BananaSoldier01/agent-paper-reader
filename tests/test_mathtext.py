"""MathText dollar splitting: currency and escaped \\$ stay prose; $math$ and $$ stay math."""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MATHTEXT = ROOT / 'dev' / 'web' / 'src' / 'mathtext.mjs'


def split_math(src):
    script = (
        "import { splitMath } from " + json.dumps(MATHTEXT.as_uri()) + ";\n"
        "const src = " + json.dumps(src) + ";\n"
        "process.stdout.write(JSON.stringify(splitMath(src)));\n"
    )
    result = subprocess.run(
        ['node', '--input-type=module', '-e', script],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def joined(src):
    parts = split_math(src)
    return ''.join(p['value'] if p['type'] == 'text' else f"${p['value']}$" if p['type'] == 'inline' else f"$${p['value']}$$" for p in parts)


def test_currency_and_escaped_dollars_stay_prose():
    journal = 'spend $30, $50, or even $100 to prepare an article'
    parts = split_math(journal)
    assert parts == [{'type': 'text', 'value': journal}]
    assert all(p['type'] == 'text' for p in parts)
    assert '$30' in journal and '$50' in journal and '$100' in journal

    cloud = (
        'At an average of $0.015 per article on Google Cloud, '
        'it would cost $30,000 every time we wanted to reconvert the entire arXiv corpus.'
    )
    cloud_parts = split_math(cloud)
    assert cloud_parts == [{'type': 'text', 'value': cloud}]
    assert '$0.015' in cloud and '$30,000' in cloud and 'Google Cloud' in cloud

    escaped = r'spend \$30, \$50, or even \$100'
    assert split_math(escaped) == [{'type': 'text', 'value': escaped}]
    escaped_cloud = r'average of \$0.015 per article on Google Cloud, it would cost \$30,000 every time'
    assert split_math(escaped_cloud) == [{'type': 'text', 'value': escaped_cloud}]

    mixed = 'spend $30 then use $h(p)$.'
    mixed_parts = split_math(mixed)
    assert mixed_parts[0] == {'type': 'text', 'value': 'spend $30 then use '}
    assert mixed_parts[1] == {'type': 'inline', 'value': 'h(p)'}
    assert mixed_parts[2] == {'type': 'text', 'value': '.'}
    assert not any(p['type'] != 'text' and '30' in p['value'] and 'h(p)' in p['value'] for p in mixed_parts)


def test_real_inline_and_display_math():
    inline = split_math('hash $h(p)$ in the domain')
    assert inline == [
        {'type': 'text', 'value': 'hash '},
        {'type': 'inline', 'value': 'h(p)'},
        {'type': 'text', 'value': ' in the domain'},
    ]
    display = split_math('$$h(p)=1$$')
    assert display == [{'type': 'display', 'value': 'h(p)=1'}]
    both = split_math(r'see $h_{1}$ and $$\Phi[q]$$.')
    assert [p['type'] for p in both] == ['text', 'inline', 'text', 'display', 'text']
    assert both[1]['value'] == 'h_{1}'
    assert both[3]['value'] == r'\Phi[q]'
    # A digit can still be math when it is a closed expression, not a price.
    assert split_math('$1+2$') == [{'type': 'inline', 'value': '1+2'}]
    assert split_math(r'$3\times 4$') == [{'type': 'inline', 'value': r'3\times 4'}]
    assert split_math('$1,2$') == [{'type': 'inline', 'value': '1,2'}]
    assert split_math(r'$a\$b$') == [{'type': 'inline', 'value': r'a\$b'}]
    multiline = '$$\na=b\n$$'
    assert split_math(multiline) == [{'type': 'display', 'value': '\na=b\n'}]
    arrow = 'pipeline from TeX $\\rightarrow$ PDF has poor support'
    arrow_parts = split_math(arrow)
    assert arrow_parts[1] == {'type': 'inline', 'value': r'\rightarrow'}
    bar = r'denoted by ${\mathbb{Z}}_{\bar{u}}^{d}={[0\ldots (\bar{u}-1)]}^{d}$.'
    bar_parts = split_math(bar)
    assert bar_parts[1]['type'] == 'inline' and r'\bar{u}-1' in bar_parts[1]['value']
    display_h = r'$$h(p)={h}_{0}(p)+\Phi [{h}_{1}(p)] ,$$'
    assert split_math(display_h) == [{'type': 'display', 'value': r'h(p)={h}_{0}(p)+\Phi [{h}_{1}(p)] ,'}]
    # Re-joining delimiters round-trips the cases above.
    assert joined('hash $h(p)$ in the domain') == 'hash $h(p)$ in the domain'
    assert joined('$$h(p)=1$$') == '$$h(p)=1$$'


def test_shipped_reader_does_not_use_greedy_dollar_split():
    assets = ROOT / 'assets' / 'reader' / 'assets'
    bundles = sorted(assets.glob('index-*.js'))
    assert len(bundles) == 1
    js = bundles[0].read_text(encoding='utf-8', errors='replace')
    assert r'[^$\n]+' not in js
    html = (ROOT / 'assets' / 'reader' / 'index.html').read_text(encoding='utf-8')
    assert bundles[0].name in html
