"""Three workflows over a shared inventory."""
import csv
import io
from collections import Counter

import streamlit as st

from antibody_manager.domain import compatible, recipe, tokens
from antibody_manager.store import Store
from antibody_manager.auth import require_login


def label(a):
    return f"{a['name']} · {a['company']} · {a['lot'] or 'no lot'} · #{a['id']}"


def filter_inventory(rows, prefix, fields):
    search = st.text_input('Search name, catalog or lot', key=f'{prefix}_search').casefold().strip()
    result = [a for a in rows if search in ' '.join(str(a[k]) for k in ('name', 'catalog', 'lot')).casefold()]
    columns = st.columns(len(fields))
    for column, (field, title) in zip(columns, fields):
        options = sorted({part.strip() for a in rows for part in (a['dilutions'].keys() if field == 'experiment' else a[field].split(',')) if part.strip()})
        chosen = column.multiselect(title, options, key=f'{prefix}_{field}')
        if chosen:
            result = [a for a in result if tokens(','.join(a['dilutions']) if field == 'experiment' else a[field]) & {x.casefold() for x in chosen}]
    return result


def table(rows):
    if not rows:
        st.info('No antibodies match. Add inventory or adjust the filters.')
        return
    st.dataframe([{'ID': a['id'], 'Name': a['name'], 'Type': a['kind'], 'Host': a['host'],
                   'Reactivity': a['reactivity'], 'Company': a['company'], 'Location': a['location'],
                   'Color': a['color'], 'Dilutions': ', '.join(f'{k} 1:{v}' for k, v in a['dilutions'].items()),
                   'Rating / 5': round(a['rating'], 2) if a['rating'] else None, 'Reviews': a['reviews']} for a in rows],
                 hide_index=True, width='stretch')


def planning(store):
    st.header('Experimental planning')
    rows = store.antibodies()
    st.subheader('1. Choose primary antibodies')
    primaries = filter_inventory([a for a in rows if a['kind'] == 'Primary'], 'primary',
                                [('reactivity', 'Reactivity'), ('host', 'Host'), ('company', 'Company'), ('experiment', 'Experiment')])
    table(primaries)
    selected = st.multiselect('Primary antibodies', [a['id'] for a in primaries], format_func=lambda i: label(next(a for a in primaries if a['id'] == i)))
    selected_primary = [a for a in primaries if a['id'] in selected]
    if not selected_primary:
        return
    st.subheader('2. Choose secondary antibodies')
    candidates = [a for a in rows if any(compatible(p, a) for p in selected_primary)]
    candidates = filter_inventory(candidates, 'secondary', [('reactivity', 'Reactivity'), ('color', 'Color / conjugate'), ('host', 'Host'), ('company', 'Company')])
    table(candidates)
    secondary_ids = st.multiselect('Secondary antibodies', [a['id'] for a in candidates], format_func=lambda i: label(next(a for a in candidates if a['id'] == i)))
    secondaries = [a for a in candidates if a['id'] in secondary_ids]
    for primary in selected_primary:
        matches = [s['name'] for s in secondaries if compatible(primary, s)]
        if matches:
            st.caption(f"{primary['name']} → {', '.join(matches)}")
        else:
            st.warning(f"No secondary selected for {primary['name']} ({primary['host']}).")
    st.caption('Matching uses primary host and secondary reactivity only. Confirm isotype, application and cross-adsorption requirements yourself.')
    if any(n > 1 for n in Counter(p['host'].casefold() for p in selected_primary).values()):
        st.warning('Some primaries share a host. Standard secondary detection may not distinguish them in a multiplex experiment.')
    colors = [s['color'].casefold() for s in secondaries if s['color']]
    if len(colors) != len(set(colors)):
        st.warning('Selected secondaries share a color/conjugate. Check channel separation.')
    st.subheader('3. Calculate solutions')
    experiment = st.text_input('Experiment / application', value='IF')
    cols = st.columns(3)
    samples = cols[0].number_input('Number of samples', min_value=1, value=1, step=1)
    volume = cols[1].number_input('Final volume per sample (µL)', min_value=0.1, value=200.0)
    excess = cols[2].number_input('Extra solution (%)', min_value=0.0, value=10.0)
    st.caption('Each row is a separate antibody solution. Total volume includes antibody stock; diluent = final volume − stock. Do not sum these rows as a cocktail.')
    output = []
    for a in selected_primary + secondaries:
        suggestion = next((v for k, v in a['dilutions'].items() if k.casefold() == experiment.strip().casefold()), None)
        if suggestion is None:
            st.warning(f"No dilution recorded for {a['name']} in {experiment or 'this application'}. Enter a validated dilution.")
        denominator = st.number_input(f"{a['name']} dilution 1:N", min_value=1, value=suggestion or 1,
                                      key=f"dilution_{a['id']}_{experiment}")
        confirmed = suggestion is not None or st.checkbox(f"I have supplied a dilution for {a['name']}", key=f"confirm_{a['id']}_{experiment}")
        if confirmed:
            amounts = recipe(samples, volume, denominator, excess)
            output.append({'Antibody': label(a), 'Location': a['location'], 'Dilution': f'1:{denominator}', **{k: round(v, 4) for k, v in amounts.items()}})
            if amounts['stock_ul'] < 1:
                st.warning(f"{a['name']}: stock volume is below 1 µL. Consider a validated intermediate dilution.")
    if output:
        st.dataframe(output, hide_index=True, width='stretch')
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
        st.download_button('Download solution plan (CSV)', buffer.getvalue(), 'antibody_plan.csv', 'text/csv')


def antibody_form(store, existing=None):
    a = existing or {}
    with st.form(f"edit_{a.get('id', 'new')}"):
        cols = st.columns(2)
        data = {'name': cols[0].text_input('Antibody name *', a.get('name', '')),
                'kind': cols[1].selectbox('Type', ['Primary', 'Secondary'], index=1 if a.get('kind') == 'Secondary' else 0)}
        for field, title in [('host', 'Host species *'), ('reactivity', 'Reactivity species (comma-separated) *'),
                             ('company', 'Company'), ('catalog', 'Catalog number'), ('lot', 'Lot / aliquot'),
                             ('location', 'Storage location (freezer / box / position) *'), ('color', 'Color / conjugate')]:
            data[field] = st.text_input(title, a.get(field, ''))
        dilution_text = st.text_area('Dilutions: one application=denominator per line', '\n'.join(f'{k}={v}' for k, v in a.get('dilutions', {}).items()), placeholder='IF=500\nWB=1000')
        data['notes'] = st.text_area('Notes / validation information', a.get('notes', ''))
        if st.form_submit_button('Save antibody'):
            try:
                data['dilutions'] = {}
                for line in dilution_text.splitlines():
                    if line.strip():
                        application, denominator = line.split('=', 1)
                        data['dilutions'][application.strip()] = int(denominator.strip())
                store.save(data, a.get('id'))
                st.success('Antibody saved.')
                st.rerun()
            except ValueError as exc:
                st.error(f'Could not save: {exc}')


def storage(store):
    st.header('Storage overview')
    show_archived = st.checkbox('Include archived antibodies')
    all_rows = store.antibodies(show_archived)
    rows = filter_inventory(all_rows, 'storage', [('kind', 'Type'), ('host', 'Host'), ('reactivity', 'Reactivity'), ('company', 'Company'), ('location', 'Location')])
    table(rows)
    with st.expander('Add a new antibody'):
        antibody_form(store)
    if rows:
        selected = st.selectbox('Antibody to manage', rows, format_func=label)
        with st.expander('Modify antibody', expanded=False):
            antibody_form(store, selected)
        st.caption('Deletion archives the inventory entry, preserving its ratings and result images. Archived entries are excluded from planning.')
        confirm = st.checkbox('Confirm archive / restore', key=f"archive_confirm_{selected['id']}")
        if st.button('Restore antibody' if selected['archived'] else 'Delete from active inventory', disabled=not confirm):
            store.archive(selected['id'], not selected['archived'])
            st.rerun()


def ratings(store):
    st.header('Antibody ratings & results')
    rows = filter_inventory(store.antibodies(True), 'ratings', [('kind', 'Type'), ('company', 'Company'), ('experiment', 'Recorded applications')])
    if not rows:
        st.info('Add antibodies in Storage overview to start reviewing.')
        return
    a = st.selectbox('Antibody', rows, format_func=label)
    st.metric('Average rating', f"{a['rating']:.2f} / 5" if a['rating'] else 'No ratings', help=f"{a['reviews']} reviews across all applications")
    st.caption(f"Storage: {a['location']} | {'Archived' if a['archived'] else 'Active'}")
    with st.form(f"review_{a['id']}", clear_on_submit=True):
        st.subheader('Add a review')
        author = st.session_state['_auth']['username']
        st.caption(f'Reviewer: {author}')
        experiment = st.text_input('Experiment / application *')
        score = st.select_slider('Rating', options=[1, 2, 3, 4, 5], value=3, format_func=lambda n: '★' * n + '☆' * (5 - n))
        comment = st.text_area('Results, dilution used, sample and protocol details')
        images = st.file_uploader('Result pictures (PNG/JPEG, max 5, 10 MB each)', type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)
        if st.form_submit_button('Publish review'):
            try:
                store.add_review(a['id'], author, experiment, score, comment, [(f.name, f.getvalue()) for f in images])
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    reviews = store.reviews(a['id'])
    applications = sorted({r['experiment'] for r in reviews})
    chosen = st.multiselect('Filter reviews by application', applications)
    for review in reviews:
        if chosen and review['experiment'] not in chosen:
            continue
        with st.expander(f"{'★' * review['rating']} · {review['experiment']} · {review['author']} · {review['created']}"):
            st.write(review['comment'] or 'No written comments.')
            for image in review['images']:
                st.image(image['data'], caption=image['name'])
                st.download_button('Download image', image['data'], image['name'], key=f"image_{image['id']}")
    if not reviews:
        st.info('No reviews yet.')


def main():
    st.set_page_config(page_title='Antibody Manager', page_icon='🧪', layout='wide')
    require_login()
    store = Store()
    st.sidebar.title('🧪 Antibody Manager')
    page = st.sidebar.radio('Workspace', ['Experimental planning', 'Storage overview', 'AB Rating'])
    st.sidebar.caption('Plan experiments · locate inventory · share results')
    if not store.antibodies(True):
        st.sidebar.info('Start by adding your antibodies, or explore fictional demo data.')
        if st.sidebar.button('Load fictional demo inventory'):
            store.seed_demo()
            st.rerun()
    {'Experimental planning': planning, 'Storage overview': storage, 'AB Rating': ratings}[page](store)
