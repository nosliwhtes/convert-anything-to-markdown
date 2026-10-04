"""Offline regressions for parsing and per-input failures."""
import json
from pathlib import Path

import pytest

from convert_anything_md.cli import _resolve_inputs, main
from convert_anything_md.extractors.epub import EpubExtractor
from convert_anything_md.extractors.office import CsvExtractor
from convert_anything_md.router import convert_batch, convert_file


@pytest.mark.parametrize(('text', 'suffix', 'delimiter'), [
    ('a,"b;c"\n1,"2;3"\n', '.csv', ','),
    ('a,"b;c\nd"\n1,"2;3"\n', '.csv', ','),
    ('name\nhello\n', '.csv', ','),
    ('"a;b"\n"c;d"\n', '.csv', ','),
    ('a;b\n1;2\n', '.csv', ';'),
    ('a;1,2\nb;3,4\n', '.csv', ';'),
    ('a\tb\n1\t2\n', '.tsv', '\t'),
    ('a\tb\n1\t2\n', '.csv', '\t'),
    ('a|b\n1|2\n', '.csv', '|'),
])
def test_csv_delimiter(tmp_path, text, suffix, delimiter):
    source = tmp_path / ('data' + suffix)
    source.write_text(text)
    result = CsvExtractor().extract(source)
    assert result.extra['delimiter'] == delimiter
    if text.startswith('a,"b;c"'):
        assert '| a | b;c |' in result.markdown


@pytest.mark.parametrize('stage', ['write', 'pick', 'frontmatter'])
def test_output_filesystem_failure_continues_batch(tmp_path, monkeypatch, stage):
    import convert_anything_md.router as router
    bad, good = tmp_path / 'bad.txt', tmp_path / 'good.txt'
    bad.write_text('bad')
    good.write_text('good')
    if stage == 'write':
        original = Path.write_text

        def write(path, *args, **kwargs):
            if path.name == 'bad.md':
                raise PermissionError('write denied')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'write_text', write)
    elif stage == 'pick':
        original = router._pick_output_path

        def pick(out_dir, source, **kwargs):
            if source == bad:
                raise OSError('cannot stat output')
            return original(out_dir, source, **kwargs)
        monkeypatch.setattr(router, '_pick_output_path', pick)
    else:
        original = router.build_frontmatter

        def frontmatter(**kwargs):
            if kwargs['source'] == bad:
                raise OSError('source disappeared')
            return original(**kwargs)
        monkeypatch.setattr(router, 'build_frontmatter', frontmatter)
    outcomes = convert_batch([bad, good], output_dir=tmp_path / 'out')
    assert [o.ok for o in outcomes] == [False, True]
    assert outcomes[0].output is None
    assert outcomes[0].engine == 'plaintext'
    assert outcomes[0].error.startswith('cannot write output:')


def test_batch_catches_detection_oserror(tmp_path, monkeypatch):
    import convert_anything_md.router as router
    bad, good = tmp_path / 'bad.txt', tmp_path / 'good.txt'
    bad.write_text('bad')
    good.write_text('good')
    original = router.detect_kind

    def detect(path):
        if path == bad:
            raise PermissionError('read denied')
        return original(path)
    monkeypatch.setattr(router, 'detect_kind', detect)
    assert not convert_file(bad, dry_run=True).ok
    assert [o.ok for o in convert_batch([bad, good], dry_run=True)] == [False, True]


@pytest.mark.parametrize('valid', [False, True])
def test_cli_reports_all_input_failures(tmp_path, capsys, valid):
    good = tmp_path / 'good.txt'
    good.write_text('hello')
    args = [str(tmp_path / 'missing'), str(tmp_path), '--json', '--dry-run']
    if valid:
        args.insert(0, str(good))
    assert main(args) == (1 if valid else 2)
    result = json.loads(capsys.readouterr().out)
    assert result['summary'] == {'total': 2 + valid, 'succeeded': int(valid), 'failed': 2}
    assert all(r['error'] for r in result['results'] if not r['ok'])


def test_duplicate_globs_are_not_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'note.txt').write_text('hello')
    paths, missing, dirs = _resolve_inputs(['*.txt', './*.txt'])
    assert paths == [tmp_path / 'note.txt']
    assert missing == dirs == []


def test_epub_spine_order_and_missing_fallback(tmp_path, monkeypatch):
    from ebooklib import epub
    book = epub.EpubBook()
    first = epub.EpubHtml(uid='first', file_name='first.xhtml')
    first.content = '<h1>First chapter</h1>'
    second = epub.EpubHtml(uid='second', file_name='second.xhtml')
    second.content = '<h1>Second chapter</h1>'
    extra = epub.EpubHtml(uid='extra', file_name='extra.xhtml')
    extra.content = '<p>Unlisted appendix</p>'
    for item in [second, extra, first]:
        book.add_item(item)
    book.spine = [('first', 'yes'), ('second', 'yes')]
    monkeypatch.setattr(epub, 'read_epub', lambda path: book)
    result = EpubExtractor().extract(tmp_path / 'book.epub')
    assert result.markdown.index('First chapter') < result.markdown.index('Second chapter')
    assert 'Unlisted appendix' not in result.markdown
    assert result.warnings == []
    book.spine = [('missing', 'yes')]
    fallback = EpubExtractor().extract(tmp_path / 'book.epub')
    assert 'Unlisted appendix' in fallback.markdown
    assert any('manifest order' in warning for warning in fallback.warnings)


def test_output_directory_failure(tmp_path):
    source = tmp_path / 'note.txt'
    source.write_text('hello')
    outcome = convert_file(source, output_dir=source)
    assert not outcome.ok
    assert 'cannot prepare output directory' in outcome.error


def test_overlapping_recursive_directories_are_not_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    docs = tmp_path / 'docs'
    docs.mkdir()
    (docs / 'note.txt').write_text('hello')
    paths, missing, dirs = _resolve_inputs(['docs', 'd*'], recursive=True)
    assert paths == [docs / 'note.txt']
    assert missing == dirs == []


def test_cli_write_failure_is_json(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'note.txt'
    source.write_text('hello')

    def fail_write(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(Path, 'write_text', fail_write)
    assert main([str(source), '--json', '-o', str(tmp_path / 'out')]) == 2
    envelope = json.loads(capsys.readouterr().out)
    assert envelope['summary']['failed'] == 1
    assert envelope['results'][0]['output'] is None
    assert 'disk full' in envelope['results'][0]['error']
