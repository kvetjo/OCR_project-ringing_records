import json
import csv
import os

def flatten_dict(d, parent_key='', sep='_'):
    # Flatten nested dictionaries (e.g., age.written to age_written)
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            if v:
                items.extend(flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, None))
        else:
            items.append((new_key, v))
    return dict(items)

# Ranking used to pick the worst severity when a record has several 'details' entries
SEVERITY_ORDER = {'info': 0, 'low': 1, 'medium': 2, 'high': 3, 'critical': 4}


def summarize_details(details, sep=" | "):
    # Collapse the 'details' list into flat columns (one row per record, not per detail)
    if not details:
        return {
            'details_count': 0,
            'details_fields': None,
            'details_rules': None,
            'details_severity': None,
            'details_evidence': None,
        }

    fields = [str(d.get('field')) for d in details]
    rules = [str(d.get('rule')) for d in details]
    severities = [d.get('severity') for d in details if d.get('severity')]
    evidence = [
        f"[{d.get('field')}] {d.get('evidence')}" if d.get('field') else str(d.get('evidence'))
        for d in details
    ]

    worst = None
    if severities:
        worst = max(severities, key=lambda s: SEVERITY_ORDER.get(str(s).lower(), -1))

    return {
        'details_count': len(details),
        'details_fields': sep.join(fields),
        'details_rules': sep.join(rules),
        'details_severity': worst,
        'details_evidence': sep.join(evidence),
    }


def normalize_place_mbs(ringing_item):
    # 'place_mbs_dict' is either a dict or a marker string like 'loc_not_matched'.
    # Without this the marker lands in its own column and breaks the place columns.
    value = ringing_item.get('place_mbs_dict')
    if value is None or isinstance(value, dict):
        return ringing_item

    item = dict(ringing_item)
    item['place_mbs_dict'] = {'place': value, 'id': None}
    return item


def convert_jsonl_to_csv(input_filepath, output_filepath, review_filepath=None, filter_review_data=False, data_type='ringing', normalize_place=False):
    """
    data_type: str - 'ringing', 'retrap', 'ringing_review', 'retrap_review', or 'silent_errors'
    normalize_place: bool - unify 'place_mbs_dict' string markers into the place/id columns
    """
    # STEP 1: Load data to be filtered out from the review file (Only if filtering is enabled)
    review_identifiers = set()
    if filter_review_data and review_filepath and os.path.exists(review_filepath):
        with open(review_filepath, 'r', encoding='utf-8') as review_file:
            for line in review_file:
                if not line.strip():
                    continue
                try:
                    review_record = json.loads(line)
                    custom_id = review_record.get("custom_id")
                    
                    # Target id is located inside 'species_entry' or 'record_entry'
                    species_entry = review_record.get("species_entry", {})
                    record_entry = review_record.get("record_entry", {})
                    
                    # Support for both 'id_row' (ringing) and 'id_retrap' (retrap)
                    target_id = species_entry.get("id_row")
                    if target_id is None:
                        target_id = record_entry.get("id_retrap")
                    
                    if custom_id is not None and target_id is not None:
                        # Save the unique identifier pair for filtering
                        review_identifiers.add((custom_id, target_id))
                except json.JSONDecodeError:
                    print("Warning: Failed to decode a line in the review file.")
                    continue
        print(f"Loaded {len(review_identifiers)} unique records to filter out (from the review file).")
    elif filter_review_data:
        print(f"Warning: Filtering is enabled, but the file was not found at: {review_filepath}")

    all_rows = []
    all_keys = set()
    
    # STEP 2: FIRST PASS - Load main data and apply structure logic based on data_type
    with open(input_filepath, 'r', encoding='utf-8') as jsonl_file:
        for line in jsonl_file:
            if not line.strip():
                continue
            
            record = json.loads(line)
            custom_id = record.get('custom_id')
            
            # ---------------------------------------------------------
            # TYPE: RINGING
            # ---------------------------------------------------------
            if data_type == 'ringing':
                base_data = {k: v for k, v in record.items() if k != 'data'}
                
                for data_item in record.get('data', []):
                    id_row = data_item.get('id_row')
                    
                    if filter_review_data and (custom_id, id_row) in review_identifiers:
                        continue 
                    
                    item_base = {k: v for k, v in data_item.items() if k != 'ringing'}
                    
                    ringing_list = data_item.get('ringing', [])
                    if not ringing_list:
                        ringing_list = [{}]
                        
                    for ringing_item in ringing_list:
                        flat_ringing = flatten_dict(ringing_item, parent_key='ringing')
                        row = {**base_data, **item_base, **flat_ringing}
                        
                        all_rows.append(row)
                        all_keys.update(row.keys())

            # ---------------------------------------------------------
            # TYPE: RETRAP
            # ---------------------------------------------------------
            elif data_type == 'retrap':
                base_data = {k: v for k, v in record.items() if k != 'retraps'}
                flat_base_data = flatten_dict(base_data)
                
                retraps_list = record.get('retraps', [])
                if not retraps_list:
                    retraps_list = [{}]
                    
                for retrap_item in retraps_list:
                    id_retrap = retrap_item.get('id_retrap')
                    
                    if filter_review_data and (custom_id, id_retrap) in review_identifiers:
                        continue 
                    
                    flat_retrap = flatten_dict(retrap_item)
                    row = {**flat_base_data, **flat_retrap}
                    
                    all_rows.append(row)
                    all_keys.update(row.keys())

            # ---------------------------------------------------------
            # TYPE: RINGING_REVIEW / SILENT_ERRORS
            # Same shape; 'silent_errors' additionally carries a 'details' list
            # ---------------------------------------------------------
            elif data_type in ('ringing_review', 'silent_errors'):
                root_metadata = {
                    'custom_id': record.get('custom_id'),
                    'batch_id': record.get('batch_id'),
                    'reasons': ", ".join(record.get('reasons', [])),
                    'timestamp': record.get('timestamp')
                }
                
                if data_type == 'silent_errors':
                    root_metadata.update(summarize_details(record.get('details', [])))
                
                species_entry = record.get('species_entry', {})
                item_base = {k: v for k, v in species_entry.items() if k != 'ringing'}
                flat_item_base = flatten_dict(item_base)
                
                ringing_list = species_entry.get('ringing', [])
                if not ringing_list:
                    ringing_list = [{}]
                    
                for ringing_item in ringing_list:
                    if normalize_place:
                        ringing_item = normalize_place_mbs(ringing_item)
                    flat_ringing = flatten_dict(ringing_item, parent_key='ringing')
                    row = {**root_metadata, **flat_item_base, **flat_ringing}
                    
                    all_rows.append(row)
                    all_keys.update(row.keys())

            # ---------------------------------------------------------
            # TYPE: RETRAP_REVIEW
            # ---------------------------------------------------------
            elif data_type == 'retrap_review':
                root_metadata = {
                    'custom_id': record.get('custom_id'),
                    'batch_id': record.get('batch_id'),
                    'reasons': ", ".join(record.get('reasons', [])),
                    'timestamp': record.get('timestamp')
                }
                
                record_entry = record.get('record_entry', {})
                base_data = {k: v for k, v in record_entry.items() if k != 'retraps'}
                flat_base_data = flatten_dict(base_data)
                
                retraps_list = record_entry.get('retraps', [])
                if not retraps_list:
                    retraps_list = [{}]
                    
                for retrap_item in retraps_list:
                    flat_retrap = flatten_dict(retrap_item)
                    row = {**root_metadata, **flat_base_data, **flat_retrap}
                    
                    all_rows.append(row)
                    all_keys.update(row.keys())

    # STEP 3: Sort the header (priority keys first, the rest sorted alphabetically) and write to CSV
    priority_keys = [
        'custom_id', 'batch_id', 'reasons',
        'details_count', 'details_severity', 'details_fields', 'details_rules', 'details_evidence',
        'timestamp', 'row_count', 'id_row', 'id_retrap', 'serial', 'ring_no', 'species'
    ]
    header = [k for k in priority_keys if k in all_keys]
    header += sorted([k for k in all_keys if k not in priority_keys])

    with open(output_filepath, 'w', encoding='utf-8', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=header)
        
        # Write header only if data exists
        if header: 
            writer.writeheader()
        
        for row in all_rows:
            writer.writerow(row)
            
    print(f"Conversion completed. Processed and wrote {len(all_rows)} rows to CSV.")


if __name__ == "__main__":
    # Setup paths according to your environment
    input_file = r"C:\Birdlife_projects\OCR_OpenRouter\ground_truth_data\ringing_sheets\img_7_4.json"
    review_file = r"C:\Birdlife_projects\OCR_universal_project\data_output\output\ringing-subalpine_warblers-full\review-ringing-subalpine_warblers-full.jsonl"
    output_file = r"C:\Birdlife_projects\OCR_OpenRouter\ground_truth_data\ringing_sheets\img_7_4.csv"

    # Set data_type to 'ringing', 'retrap', 'ringing_review', 'retrap_review', or 'silent_errors'
    convert_jsonl_to_csv(
        input_filepath=input_file, 
        output_filepath=output_file, 
        review_filepath=review_file, 
        filter_review_data=False,      # Set to True to enable filtering
        data_type='ringing',            # <--- HERE YOU CHOOSE WHICH DATA YOU HAVE
        normalize_place=False           # 'loc_not_matched' -> ringing_place_mbs_dict_place
    )