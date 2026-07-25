import os
import re

roadmap_path = 'docs/ROADMAP_500.md'
skills_dir = 'skills'

existing_folders = set(os.listdir(skills_dir))

with open(roadmap_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
cat_counts = {}
current_cat = None

for line in lines:
    if line.startswith("## "):
        # e.g. ## multi-asset-derivatives  _(28 tracked: 28 built, 0 planned)_
        match_cat = re.match(r'##\s+([^\s]+)', line)
        if match_cat:
            current_cat = match_cat.group(1)
            cat_counts[current_cat] = {'tracked': 0, 'built': 0, 'planned': 0}
        new_lines.append(line)
        continue

    match_skill = re.search(r'-\s+\*\*(?:\[BUILT\]|\[planned\])\*\*\s+`([^`]+)`', line)
    if match_skill and current_cat:
        skill_name = match_skill.group(1)
        is_built = skill_name in existing_folders
        tag = "[BUILT]" if is_built else "[planned]"

        cat_counts[current_cat]['tracked'] += 1
        if is_built:
            cat_counts[current_cat]['built'] += 1
        else:
            cat_counts[current_cat]['planned'] += 1

        # Replace tag in line
        new_line = re.sub(r'-\s+\*\*(?:\[BUILT\]|\[planned\])\*\*', f'- **{tag}**', line)
        new_lines.append(new_line)
    else:
        new_lines.append(line)

# Now update category header lines in new_lines
final_lines = []
current_cat = None
for line in new_lines:
    if line.startswith("## "):
        match_cat = re.match(r'##\s+([^\s]+)', line)
        if match_cat:
            current_cat = match_cat.group(1)
            c = cat_counts[current_cat]
            line = f"## {current_cat}  _({c['tracked']} tracked: {c['built']} built, {c['planned']} planned)_\n"
    final_lines.append(line)

# Also update grand total
total_tracked = sum(c['tracked'] for c in cat_counts.values())
total_built = sum(c['built'] for c in cat_counts.values())
total_planned = sum(c['planned'] for c in cat_counts.values())

for i in range(len(final_lines)-1, -1, -1):
    if "Grand total:" in final_lines[i]:
        final_lines[i] = f"**Grand total: {total_tracked} skills tracked ({total_built} built, {total_planned} planned) across {len(cat_counts)} categories.**\n"
        break

with open(roadmap_path, 'w', encoding='utf-8') as f:
    f.writelines(final_lines)

print(f"Roadmap updated successfully:")
print(f"Total Tracked: {total_tracked}")
print(f"Total Built (Folders on Disk): {total_built}")
print(f"Total Planned (Missing Folders): {total_planned}")
