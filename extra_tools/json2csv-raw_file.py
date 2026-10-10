import json
import csv

# Define input and output files
input_file = r'C:\Birdlife_projects\OCR_OpenRouter\ground_truth_data\ringing_sheets\img_3_7.json'
output_file = r'C:\Birdlife_projects\OCR_OpenRouter\ground_truth_data\ringing_sheets\img_3_7.csv'

# Load JSON data
with open(input_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

# Define CSV headers, prepending 'ringing_' to flattened fields
headers = [
    'id_row', 'serial', 'ring_no', 'species',
    'ringing_date', 'ringing_age', 'ringing_sex', 'ringing_wing', 
    'ringing_weight', 'ringing_time', 'ringing_initials', 'ringing_place', 'ringing_re'
]

# Write to CSV
with open(output_file, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=headers)
    writer.writeheader()
    
    for item in data:
        # Extract root properties
        row = {
            'id_row': item.get('id_row'),
            'serial': item.get('serial'),
            'ring_no': item.get('ring_no'),
            'species': item.get('species'),
        }
        
        # Flatten the nested 'ringing' array (assuming 1 item per row based on data)
        ringing_list = item.get('ringing', [])
        if ringing_list:
            r_data = ringing_list[0]
            for key in ['date', 'age', 'sex', 'wing', 'weight', 'time', 'initials', 'place', 're']:
                row[f'ringing_{key}'] = r_data.get(key)
                
        writer.writerow(row)

print("Data successfully flattened and saved to output.csv")