import json
import re

def get_vimeo_mapping(file_path='vimeo_master_list.json'):
    """Parses the vimeo master list and returns a mapping of date_slug to video_url."""
    try:
        with open(file_path, 'r') as f:
            vimeo_data = json.load(f)
            
        mapping = {}
        for video_id, title in vimeo_data.items():
            # Match YYYYMMDD
            match = re.search(r'(\d{4})(\d{2})(\d{2})', title)
            if match:
                y, m, d = match.groups()
                date_slug = f"{y}-{m}-{d}"
                mapping[date_slug] = f"https://vimeo.com/{video_id}"
            else:
                # Try Month Day Year
                months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
                match = re.search(rf'({"|".join(months)})\s+(\d{{1,2}})(?:,)?\s+(\d{{4}})', title, re.I)
                if match:
                    try:
                        from datetime import datetime
                        dt = datetime.strptime(f"{match.group(1).capitalize()} {match.group(2)} {match.group(3)}", "%B %d %Y")
                        date_slug = dt.strftime("%Y-%m-%d")
                        mapping[date_slug] = f"https://vimeo.com/{video_id}"
                    except: pass
        
        return mapping
    except Exception as e:
        print(f"Error parsing Vimeo list: {e}")
        return {}

if __name__ == "__main__":
    mapping = get_vimeo_mapping()
    print(json.dumps(mapping, indent=2))
