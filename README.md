# FabricAgent

A GitHub Copilot Agent (Python) that connects to the **Microsoft Fabric REST API**
to list and manage Fabric workspaces.

---

## Project structure

```
FabricAgent/
├── src/fabric_agent/
│   ├── __init__.py
│   ├── auth.py              # MSAL authentication (Service Principal + Device Code)
│   ├── fabric_client.py     # Typed Fabric REST API client with pagination
│   └── list_workspaces.py   # Runnable script + importable function
├── tests/
│   ├── test_auth.py
│   └── test_fabric_client.py
├── .env.example             # Credential template — copy to .env, never commit .env
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your real values
```

### 3. Run

```bash
# Human-readable table output
python -m fabric_agent.list_workspaces

# Machine-readable JSON output
python -m fabric_agent.list_workspaces --json

# Verbose debug logging
LOG_LEVEL=DEBUG python -m fabric_agent.list_workspaces
```

---

## Authentication

### Option A — Service Principal (recommended for automation / CI)

| Env Variable           | Description                              |
|------------------------|------------------------------------------|
| `FABRIC_TENANT_ID`     | Azure AD / Entra ID tenant (directory) ID |
| `FABRIC_CLIENT_ID`     | App registration client ID               |
| `FABRIC_CLIENT_SECRET` | App registration client secret           |
| `FABRIC_AUTH_FLOW`     | Set to `service_principal`               |

**Setup in Entra ID:**

1. **App Registration** → New registration (e.g. `fabric-copilot-agent`)
2. **Certificates & Secrets** → New client secret → copy the value
3. **API Permissions** → Add permission → APIs my organisation uses
   → search **"Power BI Service"** → Delegated → `Tenant.Read.All` *(or grant via SP workspace role)*
4. **Grant admin consent**
5. In Fabric, add the SP as **Workspace member** (Admin/Member/Contributor/Viewer)
   — OR assign the **Fabric Administrator** Entra role to list ALL workspaces

> ⚠️ A service principal added to a workspace via API only sees *that* workspace.  
> To list all workspaces tenant-wide, the SP needs the **Fabric Administrator** Entra role.

### Option B — Device Code / Delegated (interactive, for development)

```env
FABRIC_AUTH_FLOW=device_code
FABRIC_TENANT_ID=your-tenant-id
FABRIC_CLIENT_ID=your-app-client-id
```

Enable **"Allow public client flows"** on the App Registration.  
The script will print a URL + code — open the URL, sign in, and the token is returned automatically.

---

## API reference

| Operation          | HTTP Method | Endpoint                                      |
|--------------------|-------------|-----------------------------------------------|
| List workspaces    | `GET`       | `https://api.fabric.microsoft.com/v1/workspaces` |
| Get workspace      | `GET`       | `https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}` |

**Authentication scope:** `https://api.fabric.microsoft.com/.default`

Official docs: https://learn.microsoft.com/en-us/rest/api/fabric/core/workspaces

---

## Using in your Copilot agent

```python
from fabric_agent.list_workspaces import list_workspaces_for_principal

# Returns a list of dicts — pass directly as Copilot agent context
workspaces = list_workspaces_for_principal(output_format="json")
# [{"id": "...", "displayName": "...", "type": "Workspace", "state": "Active", ...}]
```

---

## Running tests

```bash
python -m pytest tests/ -v
```

---

## Security notes

- **Never commit `.env`** — it is in `.gitignore`
- In CI (GitHub Actions), inject secrets via **GitHub Secrets** → they are available as env-vars automatically
- Rotate client secrets regularly (recommended: every 90 days)
- Use **certificate credentials** instead of client secrets for highest security
- Token lifetime is ~60 minutes; `FabricClient` holds the token for its lifetime — create a new instance per agent request if needed
