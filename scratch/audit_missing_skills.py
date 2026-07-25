import os
import re

roadmap_path = 'docs/ROADMAP_500.md'
skills_dir = 'skills'

with open(roadmap_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Match `- **[BUILT]** `skill-name`` or `- **[planned]** `skill-name``
matches = re.findall(r'-\s+\*\*(?:\[BUILT\]|\[planned\])\*\*\s+`([^`]+)`', content)

existing_folders = set(os.listdir(skills_dir))
missing = [s for s in matches if s not in existing_folders]
built_in_roadmap = [s for s in matches if f"- **[BUILT]** `{s}`" in content]

print(f"Total skill entries in ROADMAP_500.md: {len(matches)}")
print(f"Total skills marked [BUILT] in ROADMAP_500.md: {len(built_in_roadmap)}")
print(f"Actual folders on disk in skills/: {len(existing_folders)}")
print(f"Missing physical folders on disk: {len(missing)}")

print("\n--- Missing Folders Breakdown by Category ---")
cat = "Unknown"
missing_by_cat = {}
for line in content.splitlines():
    if line.startswith("## "):
        cat = line.strip()
        missing_by_cat[cat] = []
    m = re.search(r'-\s+\*\*(?:\[BUILT\]|\[planned\])\*\*\s+`([^`]+)`', line)
    if m:
        s_name = m.group(1)
        if s_name not in existing_folders:
            missing_by_cat[cat].append(s_name)

for cat_name, m_list in missing_by_cat.items():
    if m_list:
        print(f"\n{cat_name} ({len(m_list)} missing folders):")
        for s in m_list[:5]:
            print(f"  - {s}")
        if len(m_list) > 5:
            print(f"  ... and {len(m_list)-5} more")
