#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public local debug API mock service.

The service loads data_seed.json once at startup and keeps all mutations in
memory, isolated by packageId. It never writes data_seed.json back to disk.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen


VALID_CASE_SETS = {"public_root"}
SERVICE_VERSION = "2.3"
PORT_CASE_SET_MAP = {
    18081: "public_root",
}
WRITE_ENDPOINTS = (
    "/api/user/update",
    "/api/user/batch-update-status",
    "/api/user/note/create",
    "/api/user/update-status",
    "/api/user/tag/add",
    "/api/user/transfer-department",
    "/api/user/update-manager",
    "/api/user/batch-transfer-department",
)


class RuntimeStore:
    def __init__(self, case_set: str, seed: dict):
        self.case_set = case_set
        self.seed = seed
        self.by_package: dict[str, dict] = {}

    def get(self, package_id: str) -> dict:
        if package_id not in self.by_package:
            data = copy.deepcopy(self.seed)
            data["_runtime"] = {
                "tokenIndex": 0,
                "tokens": {},
                "deleteCount": {},
                "statCalls": {},
                "packageId": package_id,
            }
            self.by_package[package_id] = data
        return self.by_package[package_id]

    def reset(self, package_id: str) -> dict:
        self.by_package.pop(package_id, None)
        return self.get(package_id)

    def issue_token(self, package_id: str) -> str:
        data = self.get(package_id)
        rt = data["_runtime"]
        rt["tokenIndex"] += 1
        token = f"token_{self.case_set}_{package_id}_{rt['tokenIndex']}"
        rt["tokens"][token] = 1
        return token

    def consume_write_token(self, package_id: str, auth_header: str | None):
        data = self.get(package_id)
        tokens = data["_runtime"]["tokens"]
        if not auth_header or not auth_header.startswith("Bearer "):
            return False, {"code": 40101, "message": "unauthorized", "data": None}
        token = auth_header.split(" ", 1)[1].strip()
        remain = tokens.get(token)
        if remain is None or remain <= 0:
            return False, {"code": 40101, "message": "token expired", "data": None}
        tokens[token] = remain - 1
        return True, None


STORE: RuntimeStore
ALLOW_EMPTY_PACKAGE_ID = True


def default_package_id() -> str:
    return os.getenv("packageId", "").strip() or os.getenv("PACKAGE_ID", "").strip() or "local_default"


def ok(data):
    return 200, {"code": 0, "message": "ok", "data": data}


def not_found():
    return 404, {"code": 1004, "message": "user not found", "data": None}


def bad_request(msg="bad request"):
    return 400, {"code": 40001, "message": msg, "data": None}


def get_single(qs, key, default=None):
    values = qs.get(key)
    if not values:
        return default
    return values[0]


def users_by_id(data):
    return {u["userId"]: u for u in data.get("users", [])}


def find_user(data, uid):
    return users_by_id(data).get(uid)


def visible_user(user):
    return bool(user) and not user.get("deleted") and not user.get("archived")


def tracked_status_delta(data, user_ids, *, initial_status="active", target_status="active"):
    """Return the delta from the virtual baseline for tracked users."""

    delta = 0
    for uid in user_ids:
        user = find_user(data, uid)
        was_target = initial_status == target_status
        is_target = visible_user(user) and user.get("status") == target_status
        if is_target and not was_target:
            delta += 1
        if was_target and not is_target:
            delta -= 1
    return delta


def tracked_status_count(data, user_ids, *, target_status):
    count = 0
    for uid in user_ids:
        user = find_user(data, uid)
        if visible_user(user) and user.get("status") == target_status:
            count += 1
    return count


def visible_tag_count(data, user_ids, tag):
    return sum(
        1
        for uid in user_ids
        for user in [find_user(data, uid)]
        if visible_user(user) and tag in user.get("tags", [])
    )


def visible_tag_users(data, user_ids, tag):
    items = []
    for uid in user_ids:
        user = find_user(data, uid)
        if visible_user(user) and tag in user.get("tags", []):
            items.append({"userId": uid, "tag": tag})
    return items


def package_id_from(handler: BaseHTTPRequestHandler, qs=None):
    """Return packageId for data isolation.

    Formal service rule:
    - X-Package-Id header must be present.
    - Empty value is allowed and maps to the model runner packageId env value.
    - If no runner packageId is present, empty value maps to local_default.
    """
    header_present = "X-Package-Id" in handler.headers
    pid = handler.headers.get("X-Package-Id") if header_present else None

    if not header_present:
        return None

    if pid is None or str(pid).strip() == "":
        if ALLOW_EMPTY_PACKAGE_ID:
            return default_package_id()
        return None

    return str(pid).strip()


def package_id_value_or_default(value) -> str:
    text = "" if value is None else str(value).strip()
    return text or default_package_id()


def reset_package_id_from(handler: BaseHTTPRequestHandler) -> str | None:
    """Resolve reset packageId from X-Package-Id only."""
    return package_id_from(handler)


def paginate(items, page: int, page_size: int):
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end]


class Handler(BaseHTTPRequestHandler):
    server_version = "PackageCaseMock/2.0"

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _require_package(self, qs=None):
        pid = package_id_from(self, qs)
        if not pid:
            self._send(400, {"code": 40002, "message": "missing X-Package-Id header", "data": None})
            return None
        return pid

    def do_POST(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        path = parsed.path

        if path == "/api/debug/reset":
            package_id = reset_package_id_from(self)
            if package_id is None:
                self._send(400, {"code": 40002, "message": "missing X-Package-Id header", "data": None})
                return
            STORE.reset(package_id)
            self._send(200, {"code": 0, "message": "ok", "data": {"packageId": package_id, "reset": True}})
            return

        package_id = self._require_package(qs)
        if not package_id:
            return

        if path == "/api/auth/token":
            token = STORE.issue_token(package_id)
            self._send(200, {"code": 0, "message": "ok", "data": {"accessToken": token, "expiresIn": 300}})
            return

        ok_token, err = STORE.consume_write_token(package_id, self.headers.get("Authorization"))
        if not ok_token:
            self._send(401, err)
            return

        body = self._json_body()
        data = STORE.get(package_id)

        routes = [
            (r"^/api/user/update$", self.handle_update),
            (r"^/api/user/batch-update-status$", self.handle_batch_update_status),
            (r"^/api/user/note/create$", self.handle_note_create),
            (r"^/api/user/update-status$", self.handle_update_status),
            (r"^/api/user/restore-status/([^/]+)$", self.handle_restore_status),
            (r"^/api/user/note/delete/([^/]+)$", self.handle_note_delete),
            (r"^/api/user/archive/([^/]+)$", self.handle_archive),
            (r"^/api/user/restore/([^/]+)$", self.handle_restore_user),
            (r"^/api/user/tag/add$", self.handle_tag_add),
            (r"^/api/user/transfer-department$", self.handle_transfer_department),
            (r"^/api/user/update-manager$", self.handle_update_manager),
            (r"^/api/user/batch-transfer-department$", self.handle_batch_transfer),
        ]
        for pattern, fn in routes:
            m = re.match(pattern, path)
            if m:
                self._send(*fn(data, body, *m.groups()))
                return
        self._send(*bad_request("unknown post endpoint"))

    def do_DELETE(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        path = parsed.path
        package_id = self._require_package(qs)
        if not package_id:
            return
        ok_token, err = STORE.consume_write_token(package_id, self.headers.get("Authorization"))
        if not ok_token:
            self._send(401, err)
            return
        data = STORE.get(package_id)

        m = re.match(r"^/api/user/delete/([^/]+)$", path)
        if m:
            self._send(*self.handle_delete_user(data, {}, m.group(1)))
            return

        m = re.match(r"^/api/user/note/delete/([^/]+)$", path)
        if m:
            self._send(*self.handle_note_delete(data, {}, m.group(1)))
            return

        self._send(*bad_request("unknown delete endpoint"))

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        path = parsed.path
        if path == "/health":
            self._send(
                200,
                {
                    "code": 0,
                    "message": "ok",
                    "data": {
                        "caseSet": STORE.case_set,
                        "serviceVersion": SERVICE_VERSION,
                        "resetEmptyPackageId": True,
                        "resetRequiresPackageIdPresence": True,
                        "resetQueryPackageId": False,
                    },
                },
            )
            return
        package_id = self._require_package(qs)
        if not package_id:
            return
        data = STORE.get(package_id)

        m = re.match(r"^/api/user/detail/([^/]+)$", path)
        if m:
            self._send(*self.handle_detail(data, m.group(1), qs))
            return

        m = re.match(r"^/api/user/note/list/([^/]+)$", path)
        if m:
            self._send(*self.handle_note_list(data, m.group(1), qs))
            return

        m = re.match(r"^/api/user/tag/list/([^/]+)$", path)
        if m:
            self._send(*self.handle_tag_list(data, m.group(1), qs))
            return

        if path == "/api/user/search":
            self._send(*self.handle_search(data, qs))
            return

        if path == "/api/user/stat/active":
            self._send(*self.handle_stat_active(data, qs))
            return

        if path == "/api/user/stat/tag":
            self._send(*self.handle_stat_tag(data, qs))
            return

        if path == "/api/user/stat/department":
            self._send(*self.handle_stat_department(data, qs))
            return

        if path == "/api/user/stat/department-summary":
            self._send(*self.handle_stat_department_summary(data, qs))
            return

        if path == "/api/user/stat/manager":
            self._send(*self.handle_stat_manager(data, qs))
            return

        self._send(*bad_request("unknown get endpoint"))

    # Read handlers

    def handle_detail(self, data, user_id, qs):
        cs = STORE.case_set
        if user_id in {"U9999", "U5999", "U5998", "U6998", "U7999", "U7998", "U9998"}:
            return not_found()

        user = find_user(data, user_id)
        if not user:
            return not_found()

        # Intentional mismatch behaviours for deleted users must run before generic deleted -> 404.
        # Otherwise these cases become accidental PASS and no longer match case_expectations.
        if user.get("deleted"):
            if cs == "variant_b" and user_id in {"U6001", "U6004"}:
                result = copy.deepcopy(user)
                result["deleted"] = False
                return ok(result)
            return not_found()

        result = copy.deepcopy(user)
        verbose = str(get_single(qs, "verbose", "false")).lower() == "true"

        # Fixed mismatch behaviours by case set
        if cs == "public_root":
            if user_id == "U4001":
                result["deleted"] = False
            if user_id == "U1004":
                result.pop("title", None)
            if user_id == "U1010" and verbose:
                result["manager"] = {"userId": "M9001", "name": "WrongManager"}

        if cs == "variant_b":
            if user_id in {"U6001", "U6004"}:
                result["deleted"] = False
            if verbose and user_id != "U6003":
                result["notes"] = data.get("notesByUser", {}).get(user_id, [])

        if cs == "variant_c":
            pass

        if cs == "variant_d":
            if verbose and user_id != "U9002":
                result["notes"] = data.get("notesByUser", {}).get(user_id, [])

        return ok(result)

    def handle_search(self, data, qs):
        cs = STORE.case_set
        page = int(get_single(qs, "page", 1))
        page_size = min(int(get_single(qs, "pageSize", 20)), 100)
        status = get_single(qs, "status")
        department = get_single(qs, "department")
        tag = get_single(qs, "tag")
        keyword = get_single(qs, "keyword")
        sort_order = get_single(qs, "sortOrder", "asc")

        # Use generated ranges for test scenarios.
        if cs == "public_root":
            if status == "active":
                items = [{"userId": f"U{i}", "status": "active"} for i in range(2001, 2241)]
                return ok({"page": page, "pageSize": page_size, "total": 240, "list": paginate(items, page, page_size)})
            if department == "platform":
                items = [
                    {"userId": "U1001", "name": "Alice"},
                    {"userId": "U1002", "name": "Bob"},
                    {"userId": "U1003", "name": "Carol"},
                ]
                return ok({"page": page, "pageSize": page_size, "total": 3, "list": paginate(items, page, page_size)})
            if status == "inactive":
                first = "U9008" if sort_order == "desc" else "U9001"
                return ok({"page": page, "pageSize": page_size, "total": 9, "list": [{"userId": first}]})
            if keyword == "Alice":
                return ok({"page": page, "pageSize": page_size, "total": 1, "list": [{"userId": "U1001", "name": "Alice"}]})

        if cs == "variant_a":
            if status == "active" and department == "platform":
                tracked = ["U5001", "U5002", "U5003", "U5004", "U5005", "U5006"]
                total = 128 + tracked_status_delta(data, tracked, target_status="active")
                return ok({"page": page, "pageSize": page_size, "total": total, "list": [{"userId": "U5000"}]})
            if status == "inactive" and department == "platform":
                tracked = ["U5001", "U5002", "U5003", "U5004", "U5005", "U5006"]
                inactive_items = [
                    {"userId": uid}
                    for uid in tracked
                    for user in [find_user(data, uid)]
                    if visible_user(user) and user.get("status") == "inactive"
                ]
                total = 33 + len(inactive_items)
                return ok({"page": page, "pageSize": page_size, "total": total, "list": paginate(inactive_items or [{"userId": "U5002"}], page, page_size)})
            if status == "inactive":
                tracked = ["U5001", "U5002", "U5003", "U5004", "U5005", "U5006"]
                inactive_items = [
                    {"userId": uid}
                    for uid in tracked
                    for user in [find_user(data, uid)]
                    if visible_user(user) and user.get("status") == "inactive"
                ]
                total = 33 + len(inactive_items)
                return ok({"page": page, "pageSize": page_size, "total": total, "list": paginate(inactive_items or [{"userId": "U5009"}], page, page_size)})
            if keyword == "Zhao":
                return ok({"page": page, "pageSize": page_size, "total": 2, "list": [{"userId": "U5301", "name": "ZhaoA"}]})

        if cs == "variant_b":
            if department == "sales":
                deleted_delta = sum(1 for uid in ["U6001", "U6004"] if (find_user(data, uid) or {}).get("deleted"))
                return ok({"page": page, "pageSize": page_size, "total": 60 - deleted_delta, "list": [{"userId": "U6101"}]})
            if department == "support":
                return ok({"page": page, "pageSize": page_size, "total": 20, "list": [{"userId": "U6201"}]})
            if keyword == "Chen":
                return ok({"page": page, "pageSize": page_size, "total": 3, "list": [{"userId": "U6301", "name": "ChenA"}]})
            if keyword == "Li":
                return ok({"page": page, "pageSize": page_size, "total": 2, "list": [{"userId": "U6401", "name": "LiA"}]})

        if cs == "variant_c":
            if tag == "VIP":
                dynamic = visible_tag_users(data, ["U7001", "U7005"], "VIP")
                return ok({"page": page, "pageSize": page_size, "total": 11 + len(dynamic), "list": paginate(dynamic or [{"userId": "U7001", "tag": "VIP"}], page, page_size)})
            if tag == "ArchivedOnly":
                dynamic = visible_tag_users(data, ["U7004"], "ArchivedOnly")
                return ok({"page": page, "pageSize": page_size, "total": len(dynamic), "list": paginate(dynamic, page, page_size)})
            if keyword == "Wang":
                return ok({"page": page, "pageSize": page_size, "total": 2, "list": [{"userId": "U7601", "name": "WangA"}]})

        if cs == "variant_d":
            if status == "active":
                items = [{"userId": f"U{i}", "status": "active"} for i in range(9101, 9301)]
                return ok({"page": page, "pageSize": page_size, "total": 200, "list": paginate(items, page, page_size)})
            if tag == "VIP":
                dynamic = visible_tag_users(data, ["U9003"], "VIP")
                return ok({"page": page, "pageSize": page_size, "total": 11 + len(dynamic), "list": paginate(dynamic or [{"userId": "U9003", "tag": "VIP"}], page, page_size)})
            if keyword == "Lin":
                return ok({"page": page, "pageSize": page_size, "total": 3, "list": [{"userId": "U9701", "name": "LinA"}]})
            if status == "archived":
                archived = [
                    {"userId": uid, "status": "archived"}
                    for uid in ["U9004"]
                    for user in [find_user(data, uid)]
                    if user and user.get("status") == "archived" and not user.get("deleted")
                ]
                return ok({"page": page, "pageSize": page_size, "total": len(archived), "list": paginate(archived, page, page_size)})

        return ok({"page": page, "pageSize": page_size, "total": 0, "list": []})

    # Write handlers

    def handle_update(self, data, body, *args):
        uid = body.get("userId")
        user = find_user(data, uid)
        if user:
            user.update({k: v for k, v in body.items() if k not in {"userId"}})
            user["version"] = int(user.get("version", 1)) + 1
        if STORE.case_set == "public_root" and uid == "U1002":
            return ok({"userId": uid, "updated": True, "updatedFields": 2, "version": "5"})
        if STORE.case_set == "public_root" and uid == "U1005":
            return ok({"userId": uid, "updated": True, "updatedFields": 2, "version": 2})
        if STORE.case_set == "public_root" and uid == "U1003":
            return ok({"userId": uid, "updated": True, "updatedFields": 2, "version": 3})
        return ok({"userId": uid, "updated": True, "updatedFields": 2, "version": user.get("version", 2) if user else 2})

    def handle_update_status(self, data, body, *args):
        uid = body.get("userId")
        status = body.get("status", "inactive")
        user = find_user(data, uid)
        if user:
            user["status"] = status
        return ok({"userId": uid, "status": status, "version": 1})

    def handle_restore_status(self, data, body, user_id):
        user = find_user(data, user_id)
        if user:
            user["status"] = "active"
        return ok({"userId": user_id, "status": "active", "version": 2})

    def handle_batch_update_status(self, data, body, *args):
        ids = body.get("userIds") or body.get("ids") or []
        if STORE.case_set == "public_root" and set(ids) == {"U3003", "U3999"}:
            return ok({"updatedCount": 1, "failedCount": 1})
        for uid in ids:
            user = find_user(data, uid)
            if user:
                user["status"] = body.get("status", "inactive")
        return ok({"updatedCount": len(ids), "failedCount": 0})

    def handle_delete_user(self, data, body, user_id):
        if user_id in {"U4999", "U6999", "U6998"}:
            return not_found()
        user = find_user(data, user_id)
        if not user:
            return not_found()
        if STORE.case_set == "public_root" and user_id == "U4001":
            delete_count = data.setdefault("_runtime", {}).setdefault("deleteCount", {})
            delete_key = f"{STORE.case_set}:{user_id}"
            current_count = delete_count.get(delete_key, 0) + 1
            delete_count[delete_key] = current_count
            if current_count == 1:
                return ok({"userId": user_id, "deleted": False})
            user["deleted"] = True
            return ok({"userId": user_id, "deleted": True})
        user["deleted"] = True
        return ok({"userId": user_id, "deleted": True})

    def handle_note_create(self, data, body, *args):
        uid = body.get("userId")
        note_id = body.get("noteId") or f"N{7000 + len(data.setdefault('notesByUser', {}).get(uid, [])) + 1}"
        note = {"noteId": note_id, "userId": uid, "content": body.get("content", ""), "createdAt": "2026-05-23T00:00:00Z"}
        data.setdefault("notesByUser", {}).setdefault(uid, []).append(note)
        if STORE.case_set == "public_root" and uid == "U1006":
            return ok({"noteId": note_id, "userId": uid})
        return ok(note)

    def handle_note_list(self, data, user_id, qs):
        notes = data.setdefault("notesByUser", {}).get(user_id, [])
        return ok({"total": len(notes), "list": notes})

    def handle_note_delete(self, data, body, note_id):
        if STORE.case_set == "variant_b" and note_id == "N7005":
            return ok({"noteId": note_id, "deleted": False})
        for notes in data.setdefault("notesByUser", {}).values():
            for n in notes:
                if n.get("noteId") == note_id:
                    notes.remove(n)
                    break
        return ok({"noteId": note_id, "deleted": True})

    def handle_tag_add(self, data, body, *args):
        uid = body.get("userId")
        tag = body.get("tag")
        user = find_user(data, uid)
        if user:
            user.setdefault("tags", [])
            if tag not in user["tags"]:
                user["tags"].append(tag)
        return ok({"userId": uid, "tag": tag})

    def handle_tag_list(self, data, user_id, qs):
        user = find_user(data, user_id)
        tags = user.get("tags", []) if user else []
        return ok({"total": len(tags), "list": tags})

    def handle_archive(self, data, body, user_id):
        user = find_user(data, user_id)
        if user:
            user["archived"] = True
            user["status"] = "archived"
        return ok({"userId": user_id, "archived": True})

    def handle_restore_user(self, data, body, user_id):
        user = find_user(data, user_id)
        if user:
            user["archived"] = False
            user["status"] = "active"
        return ok({"userId": user_id, "archived": False})

    def handle_stat_active(self, data, qs):
        department = get_single(qs, "department", "")
        cs = STORE.case_set
        if cs == "public_root":
            if department == "mobile":
                return ok({"department": "mobile", "activeCount": 63})
            return ok({"department": department, "activeCount": 128})
        if cs == "variant_a":
            if department == "platform":
                tracked = ["U5001", "U5002", "U5003", "U5004", "U5005", "U5006"]
                active_count = 128 + tracked_status_delta(data, tracked, target_status="active")
                inactive_count = 33 + tracked_status_count(data, tracked, target_status="inactive")
                return ok({"department": "platform", "activeCount": active_count, "inactiveCount": inactive_count})
            if department == "mobile":
                return ok({"department": "mobile", "activeCount": 80})
        return ok({"department": department, "activeCount": 0})

    def handle_stat_department(self, data, qs):
        department = get_single(qs, "department", "")
        if STORE.case_set == "variant_d":
            if department == "platform":
                active_count = 128 + tracked_status_delta(data, ["U9001", "U9004"], target_status="active")
                return ok({"department": "platform", "activeCount": active_count, "userCount": 128})
            if department == "mobile":
                return ok({"department": "mobile", "activeCount": 80, "userCount": 80})
        return ok({"department": department, "userCount": 0})

    def handle_stat_department_summary(self, data, qs):
        department = get_single(qs, "department", "")
        if STORE.case_set == "variant_d":
            if department == "platform":
                active_total = 128 + tracked_status_delta(data, ["U9001", "U9004"], target_status="active")
                return ok({"department": "platform", "metrics": {"activeTotal": active_total, "userTotal": 128}})
            if department == "mobile":
                return ok({"department": "mobile", "metrics": {"activeTotal": 80, "userTotal": 80}})
        return ok({"department": department, "metrics": {"activeTotal": 0, "userTotal": 0}})

    def handle_stat_manager(self, data, qs):
        manager_id = get_single(qs, "managerId", "")
        return ok({"managerId": manager_id, "userCount": 10})

    def handle_stat_tag(self, data, qs):
        tag = get_single(qs, "tag", "")
        rt = data["_runtime"]
        # Runtime counters remain namespaced by case_set for traceability; the returned
        # statistics below are driven by the current packageId data state.
        key = f"{STORE.case_set}:tag:{tag}"
        rt["statCalls"][key] = rt["statCalls"].get(key, 0) + 1
        if STORE.case_set == "variant_c":
            if tag == "VIP":
                return ok({"tag": tag, "count": 11 + visible_tag_count(data, ["U7001", "U7005"], "VIP")})
            if tag == "Core":
                return ok({"tag": tag, "count": 6 + visible_tag_count(data, ["U7003"], "Core")})
        if STORE.case_set == "variant_d":
            if tag == "VIP":
                return ok({"tag": tag, "count": 11 + visible_tag_count(data, ["U9003"], "VIP")})
        return ok({"tag": tag, "count": 0})

    def handle_transfer_department(self, data, body, *args):
        uid = body.get("userId")
        to_dep = body.get("toDepartment", "mobile")
        user = find_user(data, uid)
        from_dep = user.get("department", "platform") if user else body.get("fromDepartment", "platform")
        if user:
            user["department"] = to_dep
        return ok({"userId": uid, "fromDepartment": from_dep, "toDepartment": to_dep})

    def handle_update_manager(self, data, body, *args):
        uid = body.get("userId")
        manager_id = body.get("managerId", "M9101")
        user = find_user(data, uid)
        if user:
            user["manager"] = {"userId": manager_id, "name": f"Manager{manager_id}", "department": "platform"}
        return ok({"userId": uid, "managerId": manager_id})

    def handle_batch_transfer(self, data, body, *args):
        ids = body.get("userIds") or []
        to_dep = body.get("toDepartment", "sales")
        for uid in ids:
            user = find_user(data, uid)
            if user:
                user["department"] = to_dep
        return ok({"updatedCount": len(ids), "failedCount": 0})


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def resolve_data_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if getattr(sys, "frozen", False):
        external = Path(sys.executable).resolve().parent / path
        if external.exists():
            return external
        bundle_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        return bundle_dir / path
    return Path(__file__).resolve().parent / path


def is_address_in_use(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) == 10048 or getattr(exc, "errno", None) in {48, 98, 10048}


def existing_public_api_is_healthy(host: str, port: int) -> bool:
    url = f"http://{host}:{port}/health"
    try:
        with urlopen(url, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError):
        return False
    data = payload.get("data") or {}
    return (
        payload.get("code") == 0
        and data.get("caseSet") == "public_root"
        and data.get("resetEmptyPackageId") is True
        and data.get("resetRequiresPackageIdPresence") is True
        and data.get("resetQueryPackageId") is False
    )


_WINDOWS_CONSOLE_HANDLER = None


def install_windows_console_exit_handler(server: ThreadingHTTPServer) -> None:
    """Ensure closing a double-clicked console window terminates the service."""
    if os.name != "nt":
        return
    try:
        import ctypes
    except Exception:
        return

    ctrl_close_event = 2
    ctrl_logoff_event = 5
    ctrl_shutdown_event = 6
    handled_events = {ctrl_close_event, ctrl_logoff_event, ctrl_shutdown_event}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
    def handler(ctrl_type: int) -> bool:
        if ctrl_type not in handled_events:
            return False
        try:
            server.shutdown()
            server.server_close()
        finally:
            os._exit(0)

    global _WINDOWS_CONSOLE_HANDLER
    _WINDOWS_CONSOLE_HANDLER = handler
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port_arg", nargs="?", type=int, help="Optional service port. Equivalent to --port.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("-host", dest="host_alias", default=None)
    parser.add_argument("-port", "--port", default=18081, type=int)
    parser.add_argument("-data", "--data", default="data_seed.json")
    args = parser.parse_args()
    if args.host_alias is not None:
        args.host = args.host_alias
    if args.port_arg is not None:
        args.port = args.port_arg
    case_set = PORT_CASE_SET_MAP.get(args.port, "public_root")

    seed_path = resolve_data_path(args.data)
    if not seed_path.exists():
        raise SystemExit(f"data_seed.json not found: {seed_path}")

    seed = json.loads(seed_path.read_text(encoding="utf-8"))

    global STORE
    STORE = RuntimeStore(case_set, seed)

    try:
        server = ExclusiveThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        if is_address_in_use(exc):
            if existing_public_api_is_healthy(args.host, args.port):
                print(f"Public API mock service already running on http://{args.host}:{args.port}", flush=True)
                return
            raise SystemExit(
                f"failed to start public API service: http://{args.host}:{args.port} is already in use. "
                "Close the process using this port or start with -port <other_port>."
            ) from None
        raise
    print(f"Startup success: Public API mock service listening on http://{args.host}:{args.port}", flush=True)
    print("Dataset: public_root; data_seed.json loaded into memory only", flush=True)
    print("Package rule: X-Package-Id header must be present", flush=True)
    print(f"Blank packageId maps to: {default_package_id()}", flush=True)
    print("Reset endpoint: POST /api/debug/reset with the same packageId", flush=True)
    print("Close this window or press Ctrl+C to stop the service.", flush=True)
    install_windows_console_exit_handler(server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()


# STARTUP_SANITIZE_U4001_RUNTIME_MARKERS
# 本地公开服务二进制启动时会清理 data_seed.json 中的 _deleteAttempts，并将 U4001.deleted 恢复为 False。
