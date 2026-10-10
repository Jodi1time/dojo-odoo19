#!/usr/bin/env python3
"""Prepare a private working copy of the licensed EB Gym 19 addon for Odoo 20.

This is a source conversion, not a successful installation or a database migration.
Vendor code is supplied locally by its license holder and is never downloaded or
published by this tool. The original input is not changed.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

SOURCE_VERSION = '19.0.1.3.0'
TARGET_VERSION = '20.0.1.3.0'


def convert_constraints(source):
    tree = ast.parse(source)
    changes = []
    names = []
    for cls in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
        assigned = {t.id for node in cls.body if isinstance(node, ast.Assign) for t in node.targets if isinstance(t, ast.Name)}
        for node in cls.body:
            if not isinstance(node, ast.Assign) or not any(isinstance(t, ast.Name) and t.id == '_sql_constraints' for t in node.targets):
                continue
            entries = ast.literal_eval(node.value)
            replacements = []
            for name, definition, message in entries:
                if not all(isinstance(v, str) for v in (name, definition, message)) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name):
                    raise ValueError('Unsupported constraint declaration')
                attr = '_' + name
                if attr in assigned:
                    raise ValueError('Constraint attribute collision: ' + attr)
                assigned.add(attr)
                # Keeping the original suffix retains the database constraint name.
                replacements.append(' ' * node.col_offset + f'{attr} = models.Constraint({definition!r}, {message!r})\n')
                names.append({'class': cls.name, 'name': name})
            changes.append((node.lineno - 1, node.end_lineno, ''.join(replacements)))
    lines = source.splitlines(keepends=True)
    for start, end, replacement in sorted(changes, reverse=True):
        lines[start:end] = [replacement]
    result = ''.join(lines)
    ast.parse(result)
    return result, names


def prepare(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source.name != 'eb_gym_management' or not (source / '__manifest__.py').is_file():
        raise ValueError('Point --source at the extracted eb_gym_management directory')
    if output.exists() or output == source or source in output.parents:
        raise ValueError('Output must be a new directory outside the original addon')
    manifest_source = (source / '__manifest__.py').read_text()
    manifest = ast.literal_eval(manifest_source)
    if manifest.get('version') != SOURCE_VERSION or manifest.get('license') != 'OPL-1':
        raise ValueError('Unexpected source version or license; review that archive separately')
    for file in source.rglob('*'):
        if file.is_symlink():
            raise ValueError('Symlinks are not accepted in the source addon')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='eb-gym-prepare-', dir=output.parent) as temp:
        candidate = Path(temp) / 'eb_gym_management'
        shutil.copytree(source, candidate, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        report = {'sourceVersion': SOURCE_VERSION, 'targetVersion': TARGET_VERSION,
                  'sourceManifestSha256': hashlib.sha256(manifest_source.encode()).hexdigest(),
                  'state': 'source_prepared_installation_unverified', 'convertedConstraints': []}
        for file in candidate.rglob('*.py'):
            value, constraints = convert_constraints(file.read_text())
            if constraints:
                file.write_text(value)
                report['convertedConstraints'].extend({'file': str(file.relative_to(candidate)), **item} for item in constraints)
        manifest_path = candidate / '__manifest__.py'
        manifest_path.write_text(manifest_source.replace('"' + SOURCE_VERSION + '"', '"' + TARGET_VERSION + '"', 1))
        if ast.literal_eval(manifest_path.read_text())['version'] != TARGET_VERSION:
            raise ValueError('Manifest version could not be replaced safely')
        (candidate / 'DOJANG_PREPARATION.json').write_text(json.dumps(report, indent=2) + '\n')
        candidate.rename(output)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.source, args.output), indent=2))
    except (ValueError, OSError, SyntaxError) as exc:
        parser.exit(1, str(exc) + '\n')
