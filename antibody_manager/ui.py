"""Three workflows over a shared inventory."""
import csv
import io
import os
import pandas as pd
from PIL import Image

import streamlit as st

from antibody_manager.domain import compatible, recipe, tokens, selection_conflict
from antibody_manager.store import Store
from antibody_manager.auth import require_login
from antibody_manager.onedrive import from_config as onedrive_from_config
from antibody_manager.config import database_urls

REVIEW_PICTURE_UPLOADS_ENABLED = False


def image_preview(data):
    """Create a browser-compatible first-frame preview; downloads keep the original."""
    with Image.open(io.BytesIO(data)) as picture:
        if picture.mode in ('I', 'F') or picture.mode.startswith('I;16'):
            low, high = picture.getextrema()
            if high > low:
                return picture.convert('F').point(lambda v: (v - low) * 255 / (high - low)).convert('RGB')
        return picture.convert('RGBA' if picture.mode == 'RGBA' else 'RGB')


def label(a):
    parts = [a['name']]
    if a['kind'] == 'Secondary':
        parts.append(a['color'] or 'No color')
    parts += [f"Host: {a['host']}", f"Reactivity: {a['reactivity']}",
              f"Rating: {a['rating']:.2f}/5" if a['rating'] else 'Unrated']
    return ' | '.join(parts)


def filter_inventory(rows, prefix, fields):
    search = st.text_input('Search name or catalog', key=f'{prefix}_search').casefold().strip()
    result = [a for a in rows if search in ' '.join(str(a[k]) for k in ('name', 'catalog')).casefold()]
    columns = st.columns(len(fields))
    for column, (field, title) in zip(columns, fields):
        options = sorted({part.strip() for a in rows for part in (a['dilutions'].keys() if field == 'experiment' else a[field].split(',')) if part.strip()})
        chosen = column.multiselect(title, options, key=f'{prefix}_{field}')
        if chosen:
            result = [a for a in result if (field == 'reactivity' and 'all species' in tokens(a[field])) or
                      tokens(','.join(a['dilutions']) if field == 'experiment' else a[field]) & {x.casefold() for x in chosen}]
    return result


def table(rows, key, target, storage_view=False, multi=True, smart=False, gap=30, inventory=None):
    if not rows:
        st.info('No antibodies match. Add inventory or adjust the filters.')
        return
    inventory = rows if inventory is None else inventory
    display = []
    selected_ids = list(st.session_state.get(target, [])) if multi else [st.session_state.get(target)]
    selected = [a for a in inventory if a['id'] in selected_ids]
    blocked = []
    for a in rows:
        row = {'Selected': a['id'] in selected_ids, 'Name': a['name']}
        if a['kind'] == 'Secondary':
            row['Color'] = a['color']
            row['Catalog number'] = a.get('catalog', '')
        row.update({'Host': a['host'], 'Reactivity': a['reactivity'], 'Clone': a.get('clone', ''),
                    'Company': a['company'], 'Location': a['location'],
                    'Dilutions': ', '.join(f'{k} 1:{v}' for k, v in a['dilutions'].items()),
                    'Rating / 5': round(a['rating'], 2) if a['rating'] else None})
        if storage_view:
            row['Isotype'] = a.get('isotype', '')
            if a['kind'] == 'Secondary':
                row.update({'Excitation (nm)': a.get('excitation', ''), 'Emission (nm)': a.get('emission', '')})
        conflict = selection_conflict(a, selected, gap) if smart and a not in selected else ''
        blocked.append(bool(conflict))
        if smart:
            row['Selection status'] = f'Unavailable: {conflict}' if conflict else ('Selected' if a in selected else 'Available')
        display.append(row)
    st.caption('Check rows to select antibodies below; uncheck them to remove them. Gray rows are unavailable with the current smart plan.' if multi else
               'Check a row to manage the antibody below; uncheck it to clear the selection.')
    frame = pd.DataFrame(display)
    # Editor events contain row positions. Give each new view its own widget so
    # an event can only be applied to the exact ID ordering it was rendered with.
    signature = (tuple(a['id'] for a in rows), tuple(selected_ids), smart, gap, frame.to_json())
    if st.session_state.get(f'{key}_view') != signature:
        st.session_state[f'{key}_view'] = signature
        st.session_state[f'{key}_version'] = st.session_state.get(f'{key}_version', 0) + 1
    editor_key = f"{key}_{st.session_state[f'{key}_version']}"

    def select_row():
        edits = st.session_state[editor_key].get('edited_rows', {})
        ids = list(st.session_state.get(target, [])) if multi else [st.session_state.get(target)]
        # Remove first, so changing a mutually exclusive choice works in one event.
        for index, changes in edits.items():
            if 'Selected' in changes and not changes['Selected']:
                antibody_id = rows[int(index)]['id']
                ids = [i for i in ids if i != antibody_id]
        for index, changes in edits.items():
            if not changes.get('Selected'):
                continue
            candidate = rows[int(index)]
            conflict = selection_conflict(candidate, [a for a in inventory if a['id'] in ids], gap) if smart else ''
            if conflict:
                st.session_state[f'{target}_warning'] = conflict
            elif candidate['id'] not in ids:
                ids = ids + [candidate['id']] if multi else [candidate['id']]
        st.session_state[target] = ids if multi else next((i for i in ids if i is not None), None)
        # Rebuild from the accepted IDs, including after a rejected smart choice.
        st.session_state.pop(f'{key}_view', None)

    styled = frame.style.apply(lambda row: ['color: #888888; background-color: #eeeeee' if blocked[row.name] else '' for _ in row], axis=1)
    st.data_editor(styled, hide_index=True, width='stretch', key=editor_key,
                   disabled=[column for column in frame.columns if column != 'Selected'],
                   on_change=select_row)


def choose_antibodies(title, rows, key, smart, gap, inventory=None):
    inventory = rows if inventory is None else inventory
    by_id = {a['id']: a for a in inventory}
    st.session_state[key] = [i for i in st.session_state.get(key, []) if i in by_id]
    def validate():
        accepted = []
        for i in st.session_state[key]:
            a = by_id[i]
            conflict = selection_conflict(a, accepted, gap) if smart else ''
            if conflict:
                st.session_state[f'{key}_warning'] = conflict
            else:
                accepted.append(a)
        st.session_state[key] = [a['id'] for a in accepted]
    validate()
    options = list(dict.fromkeys([a['id'] for a in rows] + st.session_state[key]))
    selected_rows = [by_id[i] for i in st.session_state[key]]
    def option_label(i):
        a = by_id[i]
        conflict = selection_conflict(a, selected_rows, gap) if smart and a not in selected_rows else ''
        return f'Unavailable: {label(a)} ({conflict})' if conflict else label(a)
    ids = st.multiselect(title, options, key=key,
                         format_func=option_label, on_change=validate)
    if st.session_state.get(f'{key}_warning'):
        st.warning(st.session_state.pop(f'{key}_warning'))
    return [by_id[i] for i in ids]


def planning(store):
    st.header('Experimental planning')
    with st.container(border=True):
        st.subheader('Smart planning')
        smart = st.checkbox('Enforce distinct primary hosts, secondary reactivity species and secondary channels', value=True)
        gap = st.number_input('Minimum wavelength separation (nm)', min_value=1, value=30)
        st.caption('Checks excitation and emission peaks separately. Missing wavelengths cannot be checked; confirm channel compatibility on your instrument.')
    rows = store.antibodies()
    st.subheader('1. Choose primary antibodies')
    primary_inventory = [a for a in rows if a['kind'] == 'Primary']
    primaries = filter_inventory(primary_inventory, 'primary',
                                [('reactivity', 'Reactivity'), ('host', 'Host'), ('company', 'Company'), ('experiment', 'Experiment')])
    table(primaries, 'primary_table', 'primary_selected', smart=smart, gap=gap, inventory=primary_inventory)
    selected_primary = choose_antibodies('Primary antibodies', primaries, 'primary_selected', smart, gap, inventory=primary_inventory)
    if not selected_primary:
        return
    st.subheader('2. Choose secondary antibodies')
    secondary_inventory = [a for a in rows if any(compatible(p, a) for p in selected_primary)]
    candidates = filter_inventory(secondary_inventory, 'secondary', [('reactivity', 'Reactivity'), ('color', 'Color / conjugate'), ('host', 'Host'), ('company', 'Company')])
    table(candidates, 'secondary_table', 'secondary_selected', smart=smart, gap=gap, inventory=secondary_inventory)
    secondaries = choose_antibodies('Secondary antibodies', candidates, 'secondary_selected', smart, gap, inventory=secondary_inventory)
    if any(not a.get('excitation') or not a.get('emission') for a in secondaries):
        st.warning('Some selected secondaries lack wavelength data. Complete it in Storage overview to check channel separation.')
    for primary in selected_primary:
        matches = [f"{s['name']} ({s['color'] or 'No color'})" for s in secondaries if compatible(primary, s)]
        if matches:
            st.caption(f"{primary['name']} → {', '.join(matches)}")
        else:
            st.warning(f"No secondary selected for {primary['name']} ({primary['host']}).")
    st.caption('Matching uses primary host and secondary reactivity only. Confirm isotype, application and cross-adsorption requirements yourself.')
    st.subheader('3. Calculate solutions')
    experiment = st.text_input('Experiment / application', value='IF')
    cols = st.columns(3)
    samples = cols[0].number_input('Number of samples', min_value=1, value=1, step=1)
    volume = cols[1].number_input('Final volume per sample (µL)', min_value=0.1, value=200.0)
    excess = cols[2].number_input('Extra solution (%)', min_value=0.0, value=10.0)
    st.caption('Each row is a separate antibody solution. Total volume includes antibody stock; diluent = final volume − stock. Do not sum these rows as a cocktail.')
    output = []
    for a in selected_primary + secondaries:
        with st.expander(f"Source details / notes: {a['name']} "):
            st.text('\n'.join(line for line in a['notes'].splitlines() if not line.startswith('Imported from ')) or 'No notes recorded.')
        suggestion = next((v for k, v in a['dilutions'].items() if k.casefold() == experiment.strip().casefold()), None)
        if suggestion is None:
            st.warning(f"No dilution recorded for {a['name']} in {experiment or 'this application'}. Enter a validated dilution.")
        denominator = st.number_input(f"{a['name']} dilution 1:N", min_value=1, value=suggestion, placeholder='Enter dilution denominator',
                                      key=f"dilution_{a['id']}_{experiment}")
        if denominator is not None:
            amounts = recipe(samples, volume, denominator, excess)
            output.append({'Type': a['kind'], 'Antibody': a['name'], 'Location': a['location'], 'Dilution': f'1:{denominator}', **{k: round(v, 4) for k, v in amounts.items()}, 'Catalog number': a['catalog']})
            if amounts['stock_ul'] < 1:
                st.warning(f"{a['name']}: stock volume is below 1 µL. Consider a validated intermediate dilution.")
    if output:
        st.dataframe(output, hide_index=True, width='stretch')
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
        st.download_button('Download solution plan (CSV)', buffer.getvalue().encode('utf-8-sig'), 'antibody_plan.csv', 'text/csv; charset=utf-8')


def antibody_form(store, existing=None):
    a = existing or {}
    with st.form(f"edit_{a.get('id', 'new')}"):
        cols = st.columns(2)
        data = {'name': cols[0].text_input('Antibody name *', a.get('name', '')),
                'kind': cols[1].selectbox('Type', ['Primary', 'Secondary'], index=1 if a.get('kind') == 'Secondary' else 0)}
        inventory = store.antibodies(True)
        for field, title in [('host', 'Host species *'), ('reactivity', 'Reactivity species *'),
                             ('company', 'Company'), ('color', 'Color / conjugate')]:
            options = sorted({v.strip() for row in inventory for v in row[field].split(',') if v.strip()})
            common = {'host': ['Rabbit', 'Mouse', 'Goat', 'Donkey', 'Rat'],
                      'reactivity': ['Human', 'Mouse', 'Rat', 'Rabbit', 'All species']}
            options = sorted(set(options + common.get(field, [])))
            defaults = [v.strip() for v in a.get(field, '').split(',') if v.strip()]
            options = sorted(set(options + defaults))
            if field == 'reactivity':
                data[field] = ', '.join(st.multiselect(title, options, default=defaults, accept_new_options=True))
            else:
                data[field] = st.selectbox(title, options, index=options.index(a[field]) if a.get(field) in options else None,
                                           accept_new_options=True) or ''
        for field, title in [('catalog', 'Catalog number'), ('clone', 'Clone'), ('isotype', 'Isotype'),
                             ('excitation', 'Excitation wavelength (nm)'), ('emission', 'Emission wavelength (nm)')]:
            data[field] = st.text_input(title, a.get(field, ''))
        data['temperature'] = st.selectbox('Storage temperature', ['-20\u00b0C', '4\u00b0C'],
                                           index=1 if a.get('temperature') == '4\u00b0C' or a.get('location', '').startswith('4\u00b0C') else 0)
        old_location = a.get('location', '').split(' / ')
        if old_location and old_location[0] in ('-20°C', '4°C'):
            old_location = old_location[1:]
        data['box'] = st.text_input('Box / location *', a.get('box', '') or (old_location[0] if old_location else ''))
        data['position'] = st.text_input('Position', a.get('position', '') or ' / '.join(old_location[1:]))
        data['location'] = ' / '.join(v for v in (data['temperature'], data['box'], data['position']) if v)
        dilution_text = st.text_area('Dilutions: one application=denominator per line', '\n'.join(f'{k}={v}' for k, v in a.get('dilutions', {}).items()), placeholder='IF=500\nWB=1000')
        data['notes'] = st.text_area('Notes / validation information', a.get('notes', ''))
        if st.form_submit_button('Save antibody'):
            try:
                if not data['box'].strip():
                    raise ValueError('A storage box / location is required.')
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
    rows = []
    for kind, tab in zip(('Primary', 'Secondary'), st.tabs(['Primary antibodies', 'Secondary antibodies'])):
        with tab:
            subset = filter_inventory([a for a in all_rows if a['kind'] == kind], f'storage_{kind}',
                                      [('host', 'Host'), ('reactivity', 'Reactivity'), ('company', 'Company'), ('location', 'Location')])
            rows.extend(subset)
            table(subset, f'storage_table_{kind}', 'storage_selected', storage_view=True, multi=False)
    with st.expander('Add a new antibody'):
        antibody_form(store)
    if rows:
        options = [a['id'] for a in rows]
        if st.session_state.get('storage_selected') not in options:
            st.session_state['storage_selected'] = None
        selected_id = st.selectbox('Antibody to manage', options, index=None, key='storage_selected', format_func=lambda i: label(next(a for a in rows if a['id'] == i)))
        if selected_id is None:
            return
        selected = next(a for a in rows if a['id'] == selected_id)
        with st.expander('Modify antibody', expanded=False):
            antibody_form(store, selected)
        st.caption('Deletion archives the inventory entry, preserving its ratings and result images. Archived entries are excluded from planning.')
        confirm = st.checkbox('Confirm archive / restore', key=f"archive_confirm_{selected['id']}")
        if st.button('Restore antibody' if selected['archived'] else 'Delete from active inventory', disabled=not confirm):
            store.archive(selected['id'], not selected['archived'])
            st.rerun()


def ratings(store):
    st.header('Antibody ratings & results')
    inventory = store.antibodies(True)
    for kind, tab in zip(('Primary', 'Secondary'), st.tabs(['Primary antibodies', 'Secondary antibodies'])):
        with tab:
            rating_panel(store, [a for a in inventory if a['kind'] == kind], f'ratings_{kind}')


def rating_panel(store, inventory, prefix):
    rows = filter_inventory(inventory, prefix, [('company', 'Company'), ('experiment', 'Recorded applications')])
    if not rows:
        st.info('Add antibodies in Storage overview to start reviewing.')
        return
    a = st.selectbox('Antibody', rows, format_func=label, key=f'{prefix}_antibody')
    if a['kind'] == 'Secondary':
        st.caption(f"Catalog number: {a['catalog'] or 'Not recorded'}")
    st.metric('Average rating', f"{a['rating']:.2f} / 5" if a['rating'] else 'No ratings', help=f"{a['reviews']} reviews across all applications")
    st.caption(f"Storage: {a['location']} | {'Archived' if a['archived'] else 'Active'}")
    with st.form(f"review_{a['id']}", clear_on_submit=True):
        st.subheader('Add a review')
        author = st.session_state['_auth']['username']
        st.caption(f'Reviewer: {author}')
        experiment = st.text_input('Experiment / application *')
        score = st.select_slider('Rating', options=[1, 2, 3, 4, 5], value=3, format_func=lambda n: '★' * n + '☆' * (5 - n))
        comment = st.text_area('Results, dilution used, sample and protocol details')
        images = []
        if REVIEW_PICTURE_UPLOADS_ENABLED:
            images = st.file_uploader('Result pictures (PNG/JPEG/TIFF, max 5, 10 MB each)', type=['png', 'jpg', 'jpeg', 'tif', 'tiff'], accept_multiple_files=True)
            st.caption('Pictures are stored at up to 2048 pixels on the longest side and 1 MB each (up to 5 MB per review). '
                       'Larger pictures become compressed JPEG review copies; small pictures stay unchanged. '
                       'Keep scientific originals separately: large 16-bit TIFFs become 8-bit display copies. '
                       'For multi-page TIFFs, export the desired frame before uploading.')
        else:
            st.caption('Picture uploads are temporarily disabled. Written reviews and ratings are available.')
        if st.form_submit_button('Publish review'):
            try:
                store.add_review(a['id'], author, experiment, score, comment, [(f.name, f.getvalue()) for f in images])
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    reviews = store.reviews(a['id'])
    applications = sorted({r['experiment'] for r in reviews})
    chosen = st.multiselect('Filter reviews by application', applications, key=f'{prefix}_review_application')
    for review in reviews:
        if chosen and review['experiment'] not in chosen:
            continue
        with st.expander(f"{'★' * review['rating']} · {review['experiment']} · {review['author']} · {review['created']}"):
            st.write(review['comment'] or 'No written comments.')
            if review['author'] == st.session_state['_auth']['username']:
                with st.form(f"update_review_{review['id']}"):
                    application = st.text_input('Experiment / application', review['experiment'])
                    score = st.select_slider('Rating', options=[1, 2, 3, 4, 5], value=review['rating'])
                    comment = st.text_area('Review details', review['comment'])
                    update_column, delete_column = st.columns(2)
                    update = update_column.form_submit_button('Update review')
                    delete = delete_column.form_submit_button('Delete review')
                    if delete:
                        try:
                            store.delete_review(review['id'], st.session_state['_auth']['username'])
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
                    if update:
                        try:
                            store.update_review(review['id'], review['author'], application, score, comment)
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
            for image in review['images']:
                try:
                    data = store.image_data(image)
                    st.image(image_preview(data), caption=f"{image['name']} ({len(data) / 1024:.0f} KB)")
                    st.download_button('Download image', data, image['name'], key=f"image_{image['id']}")
                except (ValueError, OSError):
                    st.warning(f"Picture unavailable: {image['name']}. Please try again later.")
    if not reviews:
        st.info('No reviews yet.')


def main():
    st.set_page_config(page_title='Antibody Manager', page_icon='🧪', layout='wide')
    require_login()
    database = st.secrets.get('database')
    try:
        neon_url, _ = database_urls() if not os.environ.get('ANTIBODY_DB') else (None, None)
        url = neon_url or (database.get('url', '') if database is not None else None)
        if st.secrets.get('deployment', {}).get('require_cloud_database', False) and not url:
            st.error('Configure DATABASE_URL and DATABASE_URL_UNPOOLED in Streamlit Secrets to connect the shared inventory.')
            st.stop()
        store = Store(database_url=url,
                      image_storage=onedrive_from_config(st.secrets.get('onedrive')))
        store.cleanup_images()
    except Exception:
        # Connection exceptions can contain credentials. Keep them out of the page.
        st.error('Could not initialize storage. Check the database and OneDrive settings and service availability.')
        st.stop()
    st.sidebar.title('🧪 Antibody Manager')
    st.sidebar.caption('Shared cloud inventory' if store.database_url else 'Local inventory')
    page = st.sidebar.radio('Workspace', ['Experimental planning', 'Storage overview', 'AB Rating'])
    st.sidebar.caption('Plan experiments · locate inventory · share results')
    if not store.antibodies(True):
        st.sidebar.info('Start by adding your antibodies, or explore fictional demo data.')
        if st.sidebar.button('Load fictional demo inventory'):
            store.seed_demo()
            st.rerun()
    {'Experimental planning': planning, 'Storage overview': storage, 'AB Rating': ratings}[page](store)
