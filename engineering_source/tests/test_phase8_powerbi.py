import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import phase8_powerbi as p


def test_marts_and_columns():
    assert len(p.MARTS) == 12
    assert len(set(p.MARTS)) == 12
    assert set(p.MARTS) == set(p.COLUMNS)
    assert all(len(cols) == len(set(cols)) for cols in p.COLUMNS.values())
    assert not any('patient_id' in c or 'nhs_number' in c for cols in p.COLUMNS.values() for c in cols)


def test_page_plans():
    assert len(p.PAGES) == 6
    assert all(len(page['cards']) == 4 and len(page['charts']) == 4 for page in p.PAGES)
    assert all(page['slicers'] for page in p.PAGES)
    assert len({item['title'] for item in p.PAGES}) == 6


def test_measures_all_defined():
    defined = {m[0] for measures in p.MEASURES.values() for m in measures}
    for page in p.PAGES:
        for m in page['cards']:
            assert m in defined
        for _,t,cat,ms,_ in page['charts']:
            assert t in p.MARTS
            assert cat in p.COLUMNS[t]
            assert ms in {m[0] for m in p.MEASURES[t]}


def test_reject_source_schema_drift():
    try:
        p.validate_snapshot('mart_p7_executive', [['bad']], ['unexpected'])
    except RuntimeError as ex:
        assert 'Schema drift' in str(ex)
    else:
        raise AssertionError('Schema drift was not rejected')


def test_deterministic_names():
    assert p.stable_id('abc') == p.stable_id('abc')
    assert len(p.stable_id('abc')) == 20


def test_fixture_build(tmp_path):
    (tmp_path/'powerbi').mkdir()
    r = p.run(tmp_path, 'localhost', 5432, 'postgres', 'nhs_primarycare_demo', fixture=True)
    assert r['result'] == 'fixture_pass'
    assert r['pages'] == 6
    assert len(r['visuals_per_page']) == 6
    assert sum(r['visuals_per_page']) >= 80
    assert r['mart_tables'] == 12
    # Dry-run fixture does not publish or mutate a PBIP.
    assert not list((tmp_path/'powerbi').glob('*.pbip'))
