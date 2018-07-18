from collections import OrderedDict
from google.cloud import storage
from hashlib import sha256
from staticjinja import make_site

import click
import json
import os
import sys


@click.group()
def cli():
    pass


def die(msg):
    print(msg)
    sys.exit(1)


def get_version(impl_name):
    fname = os.path.join('src', impl_name, 'version')
    if not os.path.exists(fname):
        die("Could not find version of implementation {}".format(impl_name))
    return open(fname).read().strip()


@click.command()
def postprocess():
    if not os.path.exists('report.json'):
        die("No report found to process")
    report = json.load(open('report.json'))['report']
    report['versions'] = OrderedDict(sorted({i: get_version(i) for i in ['eclair', 'lightning', 'lnd']}.items()))

    # Any unique random id would do really
    version_string = "_".join([k + "-" + v for k, v in report['versions'].items()])
    report['id'] = sha256(version_string.encode('ASCII')).hexdigest()
    with open(os.path.join('reports', report['id'] + ".json"), "w") as f:
        f.write(json.dumps(report))

    upload(report['id'] + ".json", json.dumps(report))


def group_tests(report):
    tests = report['tests']
    report['tests'] = {}
    for t in tests:
        # Strip filename
        splits = t['name'][9:].split('[')
        name = splits[0]
        config = splits[1][:-1]
        t['name'] = config
        del t['setup']
        del t['teardown']
        if name not in report['tests']:
            report['tests'][name] = {'subtests': [], 'total': 0, 'success': 0}

        report['tests'][name]['subtests'].append(t)
        report['tests'][name]['total'] += 1
        if t['outcome'] == 'passed':
            report['tests'][name]['success'] += 1
        report['tests'][name]['subtests'] = sorted(report['tests'][name]['subtests'], key=lambda x: x['name'])

    return report

def ratio_to_color(ratio):
    if ratio > 0.95:
        return 'success'
    elif ratio > 0.5:
        return 'warning'
    return 'danger'


def load_reports(template):
    reports = []
    for fname in os.listdir("reports"):
        with open(os.path.join("reports", fname), 'r') as f:
            report = json.loads(f.read())
            ratio = report['summary']['passed'] / report['summary']['num_tests']
            report['summary']['color'] = ratio_to_color(ratio)
            reports.append(group_tests(report))
    reports = sorted(reports, key=lambda x: x['created_at'])[::-1]
    return {'reports': reports}


def load_report(template):
    with open(template.filename, 'r') as f:
        report = json.loads(f.read())

    return group_tests(report)


def render_report(env, template, **report):
    report_template = env.get_template("_report.html")
    out = "%s/%s.html" % (env.outpath, report['id'])
    for k, v in report['tests'].items():
        ratio = v['success'] / v['total']
        report['tests'][k]['color'] = ratio_to_color(ratio)
    report_template.stream(**report).dump(out)


@click.command()
def html():
    global entries

    s = make_site(
        contexts=[
            ('index.html', load_reports),
            ('.*.json', load_report),
        ],
        rules=[
            ('.*.json', render_report),
        ],
        outpath='output',
        staticpaths=('static/',)
    )
    s.render()


def _get_storage_client():
    return storage.Client(project=os.getenv("GCP_PROJECT"))


def upload(filename, contents):
    client = _get_storage_client()
    bucket = client.bucket(os.getenv('GCP_STORAGE_BUCKET'))
    blob = bucket.blob(filename)

    blob.upload_from_string(
        contents,
        content_type='application/json')

    url = blob.public_url
    return url


if __name__ == '__main__':
    cli.add_command(html)
    cli.add_command(postprocess)
    cli()
