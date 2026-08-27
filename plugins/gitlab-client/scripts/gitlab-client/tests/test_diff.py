# SPDX-License-Identifier: AGPL-3.0-only
from gitlab_client.diff import OMITTED, file_status, render_compare, render_file, render_files

FLAGS = {"new_file": False, "renamed_file": False, "deleted_file": False}
NEW = {**FLAGS, "old_path": "a.py", "new_path": "a.py", "a_mode": "0", "b_mode": "100644", "diff": "@@ -0,0 +1 @@\n+print(1)\n", "new_file": True}
MOD = {**FLAGS, "old_path": "m.py", "new_path": "m.py", "a_mode": "100644", "b_mode": "100644", "diff": "@@ -1 +1 @@\n-x\n+y\n"}
DEL = {**FLAGS, "old_path": "d.py", "new_path": "d.py", "a_mode": "100644", "b_mode": "0", "diff": "@@ -1 +0,0 @@\n-gone\n", "deleted_file": True}
REN = {**FLAGS, "old_path": "old.txt", "new_path": "new.txt", "a_mode": "100644", "b_mode": "100644", "diff": "", "renamed_file": True}
BIG = {**FLAGS, "old_path": "big.bin", "new_path": "big.bin", "a_mode": "100644", "b_mode": "100644", "diff": ""}
PROJECT_JSON = {"id": 42, "path_with_namespace": "group/proj", "default_branch": "main", "web_url": "u"}


def test_file_status():
    assert [file_status(d) for d in (NEW, MOD, DEL, REN, BIG)] == ["A", "M", "D", "R", "M"]


def test_render_file_variants():
    assert render_file(NEW) == "diff --git a/a.py b/a.py\nnew file mode 100644\n--- /dev/null\n+++ b/a.py\n@@ -0,0 +1 @@\n+print(1)\n"
    assert render_file(MOD) == "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-x\n+y\n"
    assert render_file(DEL) == "diff --git a/d.py b/d.py\ndeleted file mode 100644\n--- a/d.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n"
    assert render_file(REN) == "diff --git a/old.txt b/new.txt\nrename from old.txt\nrename to new.txt\n--- a/old.txt\n+++ b/new.txt\n"
    assert render_file(BIG) == f"diff --git a/big.bin b/big.bin\n--- a/big.bin\n+++ b/big.bin\n{OMITTED}\n"
    empty_new = {**NEW, "diff": ""}  # e.g. an empty file added — not a truncation
    assert render_file(empty_new) == "diff --git a/a.py b/a.py\nnew file mode 100644\n--- /dev/null\n+++ b/a.py\n"


def test_render_files():
    assert render_files([NEW, MOD, DEL, REN]) == "A a.py\nM m.py\nD d.py\nR new.txt (from old.txt)\n4 file(s)\n"


def test_render_compare():
    payload = {"commits": [{"short_id": "abc1234", "title": "Fix it"}], "diffs": [MOD], "compare_timeout": False}
    assert render_compare(payload) == "abc1234 Fix it\n\n" + render_file(MOD)
    assert render_compare({"commits": [], "diffs": [], "compare_timeout": True}) == "[compare timed out on the server — result may be partial]\n"


def test_gl_diff_cli(run_gl, opener):
    opener.add(200, PROJECT_JSON).add(200, [NEW, MOD], {"X-Next-Page": ""})
    code, out, err = run_gl("diff", "12", "--project", "group/proj")
    assert code == 0, err
    assert out == render_file(NEW) + render_file(MOD)
    assert opener.last.full_url.endswith("/projects/42/merge_requests/12/diffs?per_page=100&page=1")

    opener.add(200, PROJECT_JSON).add(200, [NEW, MOD], {"X-Next-Page": ""})
    code, out, _ = run_gl("diff", "12", "--project", "group/proj", "--files")
    assert out == "A a.py\nM m.py\n2 file(s)\n"

    # GitLab < 15.7 has no /diffs: fall back to the deprecated /changes and surface `overflow`.
    opener.add(200, PROJECT_JSON).add(404, {"error": "404 Not Found"}).add(200, {"changes": [MOD], "overflow": True})
    code, out, err = run_gl("diff", "12", "--project", "group/proj")
    assert code == 0 and out == render_file(MOD) and "overflow=true" in err
    assert opener.last.full_url.endswith("/projects/42/merge_requests/12/changes")
    opener.add(200, PROJECT_JSON).add(403, {"message": "403 Forbidden"})
    code, _, err = run_gl("diff", "12", "--project", "group/proj")
    assert code == 1 and "403" in err  # only a 404 triggers the fallback

    opener.add(200, PROJECT_JSON).add(200, [NEW, MOD], {"X-Next-Page": ""})
    code, out, _ = run_gl("diff", "12", "--project", "group/proj", "--file", "m.py")
    assert out == render_file(MOD)

    opener.add(200, PROJECT_JSON).add(200, [NEW], {"X-Next-Page": ""})
    code, out, err = run_gl("diff", "12", "--project", "group/proj", "--file", "zzz")
    assert code == 1 and "no file 'zzz'" in err

    opener.add(200, PROJECT_JSON).add(200, [MOD])
    code, out, _ = run_gl("diff", "--commit", "abc123", "--project", "group/proj")
    assert code == 0 and out == render_file(MOD)
    assert opener.last.full_url.endswith("/projects/42/repository/commits/abc123/diff")

    opener.add(200, PROJECT_JSON).add(200, {"commits": [{"short_id": "abc1234", "title": "T"}], "diffs": [MOD], "compare_timeout": False})
    code, out, _ = run_gl("diff", "--range", "v1.0..release/2.0", "--straight", "--project", "group/proj")
    assert code == 0 and out.startswith("abc1234 T\n\n")
    assert opener.last.full_url.endswith("/repository/compare?from=v1.0&to=release%2F2.0&straight=true")

    code, _, err = run_gl("diff", "--range", "nodots", "--project", "group/proj")
    assert code == 2 and "FROM..TO" in err
    code, _, err = run_gl("diff", "--project", "group/proj")
    assert code == 2 and "MR_IID" in err
