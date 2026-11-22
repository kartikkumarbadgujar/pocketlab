"""PocketLab on Streamlit: a Streamlit dashboard for the PocketLab API.

It talks to the same FastAPI backend as the React dashboard (frontend/), so accounts, projects and runs are shared.
The API URL comes from the POCKETLAB_API_URL secret/env var (default: the hosted API on Vercel).
"""
from __future__ import annotations

import os

import pandas as pd
import requests
import streamlit as st

DEFAULT_API = "https://pocketlab-api.vercel.app/api/v1"


def _api_base() -> str:
    try:
        value = st.secrets.get("POCKETLAB_API_URL")
    except Exception:  # no secrets file locally
        value = None
    return (value or os.environ.get("POCKETLAB_API_URL") or DEFAULT_API).rstrip("/")


API = _api_base()
st.set_page_config(page_title="PocketLab", page_icon="📱", layout="wide")


class ApiError(Exception):
    pass


def _detail(resp: requests.Response) -> str:
    try:
        detail = resp.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, list):
        return " ".join(str(item.get("msg", item)) for item in detail)
    return str(detail or f"HTTP {resp.status_code}")


def _raw(method: str, path: str, token: str | None = None, **kwargs) -> requests.Response:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        return requests.request(method, f"{API}{path}", headers=headers, timeout=30, **kwargs)
    except requests.RequestException as exc:
        raise ApiError(f"Could not reach the PocketLab API at {API}: {exc.__class__.__name__}") from exc


def call(method: str, path: str, **kwargs):
    """Authenticated request; refreshes the access token once on 401."""
    resp = _raw(method, path, st.session_state.get("access"), **kwargs)
    if resp.status_code == 401 and st.session_state.get("refresh"):
        r = _raw("POST", "/auth/refresh", json={"refresh_token": st.session_state["refresh"]})
        if r.ok:
            st.session_state.update(access=r.json()["access_token"], refresh=r.json()["refresh_token"])
            resp = _raw(method, path, st.session_state["access"], **kwargs)
        else:
            sign_out(remote=False)
            raise ApiError("Your session expired. Sign in again.")
    if not resp.ok:
        raise ApiError(_detail(resp))
    return resp.json() if resp.content else None


def sign_in(email: str, password: str) -> None:
    resp = _raw("POST", "/auth/login", json={"email": email, "password": password})
    if not resp.ok:
        raise ApiError(_detail(resp))
    data = resp.json()
    st.session_state.update(access=data["access_token"], refresh=data["refresh_token"], email=email)


def sign_out(remote: bool = True) -> None:
    if remote and st.session_state.get("refresh"):
        try:
            _raw("POST", "/auth/logout", st.session_state.get("access"), json={"refresh_token": st.session_state["refresh"]})
        except ApiError:
            pass
    for key in ("access", "refresh", "email"):
        st.session_state.pop(key, None)


def table(rows, columns: list[str] | None = None, empty: str = "Nothing here yet.") -> None:
    if not rows:
        st.info(empty)
        return
    df = pd.DataFrame(rows)
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    st.dataframe(df, width="stretch", hide_index=True)


def safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ApiError as exc:
        st.error(str(exc))
        return None


# ---------------------------------------------------------------- login
def login_page() -> None:
    left, right = st.columns([1.1, 1])
    with left:
        st.caption("ANDROID RELIABILITY LAB")
        st.title("Test beyond the flagship.")
        st.write("Find the failures that only show up on real phones, under real constraints.")
        st.markdown("- Entry-level device conditions\n- India-ready network profiles\n- Measurements you can trust")
    with right:
        mode = st.radio("Account", ["Sign in", "Create account"], horizontal=True, label_visibility="collapsed")
        with st.form("auth"):
            email = st.text_input("Work email", placeholder="you@company.com")
            password = st.text_input("Password", type="password",
                                     placeholder="At least 12 characters" if mode == "Create account" else "")
            submitted = st.form_submit_button(mode, type="primary", width="stretch")
        if submitted:
            if not email or not password:
                st.error("Enter your email and password.")
                return
            try:
                if mode == "Create account":
                    if len(password) < 12:
                        st.error("Use at least 12 characters for a new password.")
                        return
                    resp = _raw("POST", "/auth/register", json={"email": email, "password": password})
                    if not resp.ok:
                        raise ApiError(_detail(resp))
                sign_in(email, password)
                st.rerun()
            except ApiError as exc:
                st.error(str(exc))
        st.caption("Use PocketLab only with applications and devices you're authorized to test.")


# ---------------------------------------------------------------- pages
def dashboard() -> None:
    st.header("Dashboard")
    projects = safe(call, "GET", "/projects", params={"limit": 100}) or []
    runs = safe(call, "GET", "/test-runs", params={"limit": 100}) or []
    devices = safe(call, "GET", "/devices", params={"limit": 100}) or []
    profiles = safe(call, "GET", "/profiles") or []
    c = st.columns(4)
    c[0].metric("Total projects", len(projects))
    c[1].metric("Total test runs", len(runs))
    c[2].metric("Failed test runs", sum(1 for r in runs if r.get("status") == "failed"))
    c[3].metric("Registered devices", len(devices))
    st.subheader("Recent test runs")
    table(sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True)[:10],
          ["id", "project_id", "status", "execution_mode", "created_at"], "No test runs yet.")
    st.subheader("Condition profiles")
    table(profiles, ["id", "name", "profile_type", "description"], "No profiles available.")


def projects_page() -> None:
    st.header("Projects")
    with st.expander("Create a project"):
        with st.form("new_project", clear_on_submit=True):
            name = st.text_input("Project name", placeholder="My Android app")
            package = st.text_input("Application package (optional)", placeholder="com.company.app")
            description = st.text_area("Description (optional)")
            if st.form_submit_button("Create project", type="primary"):
                body = {"name": name, "application_package": package or None, "description": description or None}
                if safe(call, "POST", "/projects", json=body) is not None:
                    st.success(f"Created {name}.")
    projects = safe(call, "GET", "/projects", params={"limit": 100}) or []
    table(projects, ["id", "name", "application_package", "description", "created_at"], "No projects yet.")
    if projects:
        pid = st.selectbox("Builds for project", [p["id"] for p in projects],
                           format_func=lambda i: next(p["name"] for p in projects if p["id"] == i))
        table(safe(call, "GET", f"/projects/{pid}/builds") or [],
              ["id", "version_name", "package_name", "scan_status", "size_bytes", "created_at"], "No builds uploaded.")
        st.caption("APK upload is available in the React dashboard (Projects → Upload APK).")


def runs_page() -> None:
    st.header("Test runs")
    runs = safe(call, "GET", "/test-runs", params={"limit": 100}) or []
    table(runs, ["id", "project_id", "status", "execution_mode", "requested_repetitions", "created_at"], "No test runs yet.")
    if not runs:
        return
    run_id = st.selectbox("Run details", [r["id"] for r in runs], format_func=lambda i: f"Run #{i}")
    tabs = st.tabs(["Findings", "Metrics", "Artifacts", "Environment"])
    with tabs[0]:
        table(safe(call, "GET", f"/test-runs/{run_id}/findings") or [], None, "No findings.")
    with tabs[1]:
        table(safe(call, "GET", f"/test-runs/{run_id}/metrics") or [], None, "No metrics recorded.")
    with tabs[2]:
        table(safe(call, "GET", f"/test-runs/{run_id}/artifacts") or [], None, "No artifacts.")
    with tabs[3]:
        try:
            st.json(call("GET", f"/test-runs/{run_id}/environment"))
        except ApiError as exc:
            st.info(f"No execution environment: {exc}")


def catalog_page() -> None:
    st.header("Test catalog")
    st.subheader("Scenarios")
    table(safe(call, "GET", "/scenarios") or [], ["id", "name", "scenario_category", "description"])
    st.subheader("Condition profiles")
    table(safe(call, "GET", "/profiles") or [], ["id", "name", "profile_type", "description"])


def environment_page() -> None:
    st.header("Android execution environment")
    st.write("Capabilities reported by connected Android workers. Workers run on a machine with the Android SDK; "
             "nothing here is simulated.")
    caps = safe(call, "GET", "/environment/capabilities")
    if caps is not None:
        st.json(caps)


def help_page() -> None:
    st.header("Help & documentation")
    faq = {
        "What is PocketLab?": "A lab for testing Android apps under entry-level device and Indian mobile network conditions.",
        "Where do tests run?": "On a separate Android worker (emulator or physical device) that connects to the API with a "
                               "runner token. The hosted API never runs adb or emulators itself.",
        "Are measurements real?": "Results are marked ACTUAL, EMULATED, UNSUPPORTED, UNAVAILABLE or INVALID. Nothing is "
                                  "fabricated; unsupported conditions are reported as such.",
        "Is this the same data as the web dashboard?": "Yes. This Streamlit app and the React dashboard use the same API "
                                                       "and accounts.",
    }
    for q, a in faq.items():
        with st.expander(q):
            st.write(a)
    st.caption(f"API: {API}")


PAGES = {"Dashboard": dashboard, "Projects": projects_page, "Test runs": runs_page, "Catalog": catalog_page,
         "Environment": environment_page, "Help": help_page}

if not st.session_state.get("access"):
    login_page()
else:
    with st.sidebar:
        st.markdown("### 📱 PocketLab")
        st.caption(st.session_state.get("email", ""))
        page = st.radio("Navigate", list(PAGES), label_visibility="collapsed")
        if st.button("Sign out"):
            sign_out()
            st.rerun()
    PAGES[page]()
