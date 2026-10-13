"""Documentation checks: links, code fences, table shape, ADR references, wording rules.

    python3 scripts/check_docs.py   # exit 1 on any problem
"""
import os
import sys

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import re,os,glob,sys
files=["README.md"]+sorted(glob.glob("docs/*.md"))
problems=[]
for f in files:
    txt=open(f).read(); lines=txt.split("\n")
    # broken relative links
    for l in re.findall(r'\]\(([^)#\s]+\.md)',txt):
        if not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(f),l))): problems.append(f"{f}: broken link {l}")
    # code fences balanced
    if txt.count("```")%2: problems.append(f"{f}: unbalanced code fences")
    # table column consistency (outside code fences)
    infence=False; tbl=None
    for i,line in enumerate(lines,1):
        if line.startswith("```"): infence=not infence; continue
        if infence: continue
        if line.startswith("|"):
            cells=len(re.split(r'(?<!\\)\|',line.strip()))-2
            if tbl is None: tbl=(cells,i)
            elif cells!=tbl[0]: problems.append(f"{f}:{i}: table row has {cells} cells, header (line {tbl[1]}) has {tbl[0]}")
        else: tbl=None
    # ADR references exist
    for n in set(re.findall(r'ADR-(\d{3})',txt)):
        if not re.search(rf'^## ADR-{n}:',open("docs/decision-log.md").read(),re.M): problems.append(f"{f}: references missing ADR-{n}")
    # prohibited labels used outside rule statements
    for i,line in enumerate(lines,1):
        if re.search(r'\b(dangerous|worst|criminal)\b',line,re.I) and not re.search(r'no |prohibited|never|not ',line,re.I):
            problems.append(f"{f}:{i}: prohibited wording outside a rule")
print("\n".join(problems) or "no problems")
print("checked", len(files), "files")
sys.exit(1 if problems else 0)
