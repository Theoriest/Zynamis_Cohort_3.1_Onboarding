# Inventory Management API

A backend API for an organisation to manage equipment and shared resources:
register resources, manage quantities, and approve or reject booking requests
from users — without double-booking and with strict role-based access.

Built with **Django 6.1 + Django REST Framework**, **PostgreSQL**, and
**JWT authentication**.

---

## Design decisions

This section highlights the decisions that shaped the implementation (the
"my take" notes from the task analysis), and where each lives in the code.

### Roles & access control
- **Three roles** — Admin, Resource Manager, Employee — on a custom `User`
  model (`Inventory/models.py`), defaulting to **Employee** (least privilege).
- Enforced in **two layers**: role URL prefixes *and* permission classes
  (`Inventory/permissions.py`), so management and browsing stay isolated.
  - **Admin** → full access.
  - **Resource Manager** → resource management + booking approval only.
  - **Employee** → browse resources, make and manage their own bookings.

### Bookings are employee-only (anti privilege-escalation)
- Only Employees may create bookings. Admins and Resource Managers must use
  **their employee credentials** to book — this prevents privileged accounts
  from booking and avoids accidental bookings by managers/admins.
- An employee only sees **their own** bookings; managers/admins view booking
  history per employee through the approval views.

### Only approved bookings reserve resources — FIFO
- Booking requests do **not** reserve stock. Reservation happens on **approval**,
  inside a database transaction with a row lock (`select_for_update`).
- Concurrency is resolved **FIFO**: requests are ordered by creation time, and
  the first approval(s) to exhaust stock block later overlapping requests.
  - *Example (from the brief):* 5 Den Den Mushi. Luffy requests 2, Zoro 3,
    Usopp 1 — all valid while nothing is reserved. Approving Luffy then Zoro
    consumes all 5, so Usopp's request can no longer be approved (`409`).
  - *Future:* an autonomous manager could delist available stock into memory on
    request and either hand it to the approved employee or return it to the pool
    on rejection.

### Availability model — date-aware, derived from bookings
Availability is **computed per requested date range**, not stored as static
flags. For a range `[start, end]`:

```
available = total_quantity − Σ(quantity of APPROVED bookings overlapping [start, end])
```

Two inclusive ranges overlap when `existing.start ≤ requested.end` **and**
`existing.end ≥ requested.start`. This lives in two places on `Resource`:
- `available_quantity(start, end, exclude_booking_id=None)` — the number for a
  single resource (used by booking/approval validation).
- `annotate_availability(queryset, start, end)` — annotates `available` on a
  queryset in one SQL query (used by the browse list endpoint, so you can
  filter/sort by availability for a date range).

`Resource` keeps only `total_quantity` (the real inventory size) and
`is_active` (an on/off service status). The earlier `is_booked` / `is_returned`
/ stored `available_quantity` flags were **removed**: they lived on the resource
*type* and could not express per-date-range overlap (e.g. "2 of 5 booked for
10–12 Nov, all 5 free for 20–22 Nov"). Deriving availability from the bookings
themselves fixes that and makes the brief's A/B/C projector example work
exactly.

> **Why this matters:** the same resource can be fully booked for one period
> and fully available for a non-overlapping period — and cancelling or
> rejecting a booking frees its units automatically, with no counters to keep
> in sync. And these allows the employees to pre book a resource for when its next available.

### Validation & data integrity
- `end_date > start_date` enforced both in the serializer and as a DB
  `CheckConstraint`.
- Positive booking quantities and a unique `(booking, resource)` pair as DB
  constraints.
- Requests may not exceed the units free for the period (serializer + a
  row-locked re-check at approval time).
- Rejection **requires a reason** (`rejection_reason`).
- Transactions + `select_for_update` guard the approval reservation check.

### Security / sensitive data
- Self-registration always creates an **Employee**; role is never client-set.
- Passwords are write-only; `role`/`username` are read-only on the profile, so
  users cannot escalate their own privileges.
- Secrets come from the environment (`.env`), never committed.

### Search, filtering, pagination
- Page-number pagination on resource and booking lists.
- Resources filter by category, location, availability and keyword; bookings by
  status, user, resource, and start/end date.
- Keyword search currently uses a portable `icontains`. Per the decision this is
  where `django.contrib.postgres.search` (`SearchVector`/`SearchQuery`) slots in
  now that the project runs on PostgreSQL (`django.contrib.postgres` is enabled).

---

## Tech stack

| Concern         | Choice                                   |
|-----------------|------------------------------------------|
| Framework       | Django 6.1 + Django REST Framework       |
| Auth            | JWT (`djangorestframework-simplejwt`)    |
| Database        | PostgreSQL (`psycopg` v3)                |
| API docs        | OpenAPI + Swagger UI (`drf-spectacular`) |
| Config          | `python-dotenv` (`.env`)                 |

---

## Project layout

```
InventoryManagement/
├── manage.py
├── requirements.txt
├── .env.example
├── openapi-schema.yml                 # generated OpenAPI schema
├── InventoryManagement/               # project (settings, urls, wsgi)
└── Inventory/                         # app
    ├── models.py                      # User, Category, Resource, Booking, BookingItem
    ├── serializers.py                 # validation + safe representations
    ├── permissions.py                 # role-based access control
    ├── views.py                       # role-isolated viewsets
    ├── urls.py                        # routed API endpoints
    ├── admin.py                       # back-office admin
    ├── tests.py                       # unit + integration tests
    └── management/commands/seed_strawhats.py
```

---

## Setup

### 1. Clone & create a virtualenv

Create a virtual environment, activate it, then install the dependencies from
`InventoryManagement/requirements.txt`. The activate command and the Python
executable name differ per OS — use the guide for your machine.

<details open>
<summary><strong>Linux</strong></summary>

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r InventoryManagement/requirements.txt
```
</details>

<details>
<summary><strong>macOS</strong></summary>

```bash
# If Python 3 is missing: brew install python
python3 -m venv venv
source venv/bin/activate
pip install -r InventoryManagement/requirements.txt
```
</details>

<details>
<summary><strong>Windows — PowerShell</strong></summary>

```powershell
py -3 -m venv venv
venv\Scripts\Activate.ps1
pip install -r InventoryManagement\requirements.txt
```

> If activation is blocked, allow scripts for this session first:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`
</details>

<details>
<summary><strong>Windows — Command Prompt (cmd)</strong></summary>

```bat
py -3 -m venv venv
venv\Scripts\activate.bat
pip install -r InventoryManagement\requirements.txt
```
</details>

You know it worked when your prompt is prefixed with `(venv)`. To leave the
environment later, run `deactivate`.

### 2. PostgreSQL
Create a database and user to match your `.env` (SQLite is not used):
```sql
CREATE USER inventory WITH PASSWORD 'gomugomu56' CREATEDB;
CREATE DATABASE inventory OWNER inventory;
```

### 3. Environment
```bash
cp InventoryManagement/.env.example InventoryManagement/.env
# then edit InventoryManagement/.env with real values
```

| Variable | Purpose |
|----------|---------|
| `DJANGO_SECRET_KEY` | Django secret key |
| `DJANGO_DEBUG` | `True`/`False` |
| `DJANGO_ALLOWED_HOSTS` | comma-separated hosts |
| `POSTGRES_DB/USER/PASSWORD/HOST/PORT` | database connection |
| `JWT_ACCESS_MINUTES` / `JWT_REFRESH_DAYS` | token lifetimes |

### 4. Migrate & run
```bash
cd InventoryManagement
python manage.py migrate
python manage.py createsuperuser        # optional, for the admin site
python manage.py runserver
```

---

## Example data — the Straw Hat crew

A management command seeds a demo database themed on the Straw Hat Pirates that
exercises the decisions above:

```bash
cd InventoryManagement
python manage.py seed_strawhats --flush
```

It creates:
- **Users / roles** — Robin (Admin); Nami & Franky (Resource Managers);
  Luffy, Zoro, Usopp, Sanji, Chopper, Brook, Jinbe (Employees).
  *Every account's password is `GomuGomu56`.*
- **Categories** — Navigation, Combat Equipment, Medical, Culinary,
  Ship Equipment, Communication.
- **Resources** — Log Pose, Clima-Tact, Rumble Ball Kit, Den Den Mushi, etc.
  (one deliberately **inactive** resource to demonstrate the rule).
- **Bookings** — the FIFO Den Den Mushi example: Luffy(2) + Zoro(3) approved
  exhausts all 5 units, leaving Usopp's request pending.

---

## API documentation

With the server running:
- **Swagger UI:** http://localhost:8000/api/docs/
- **ReDoc:** http://localhost:8000/api/redoc/
- **OpenAPI schema:** http://localhost:8000/api/schema/ (also checked in as
  `InventoryManagement/openapi-schema.yml`).

### Authentication

Logging in at `POST /api/auth/token/` returns **two** tokens:

- **`access`** — short-lived (30 min). This is the one you send on every
  request. Pass it as `Authorization: Bearer <access>`.
- **`refresh`** — longer-lived (1 day). *Not* sent to API endpoints; used only
  at `/api/auth/token/refresh/` to mint a new access token when the old one
  expires.

Always pass the **access** token, never the refresh one.

```bash
# obtain tokens
curl -X POST http://localhost:8000/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"username": "luffy", "password": "GomuGomu56"}'

# use the access token
curl http://localhost:8000/api/resources/ \
  -H "Authorization: Bearer <ACCESS_TOKEN>"

# refresh when the access token expires (401 token_not_valid)
curl -X POST http://localhost:8000/api/auth/token/refresh/ \
  -H "Content-Type: application/json" \
  -d '{"refresh": "<REFRESH_TOKEN>"}'
```

#### Authorizing in the Swagger docs page

On http://localhost:8000/api/docs/ the **Authorize** button uses the HTTP
`Bearer` scheme, which means Swagger adds the word `Bearer` for you:

1. Log in (via `POST /api/auth/token/` above, or the endpoint's **Try it out**)
   and copy the **`access`** token value.
2. Click **Authorize** (padlock, top-right).
3. In the value box paste **only the raw access token** — the app already sets
   the `Bearer ` prefix for you, so **do not** add `Bearer ` and **do not** wrap
   the token in quotation marks. Paste it on a single line with no spaces or
   line breaks:
   ```
   eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.<rest-of-the-access-token>
   ```
   ✅ `eyJhbGciOiJ...UXU`  ❌ `Bearer eyJhbGciOiJ...`  ❌ `"eyJhbGciOiJ..."`
4. Click **Authorize**, then **Close**. Every **Try it out** call is now
   authenticated.

> **Common mistakes:** typing `Bearer ` in the box (becomes `Bearer Bearer …`
> and fails with *"Authorization header must contain two space-delimited
> values"*); pasting the **refresh** token instead of the access token; or a
> line break inside the pasted token. If calls return `401 token_not_valid`,
> the access token has expired — log in again for a fresh one.

### Endpoint overview
| Area | Endpoint | Who |
|------|----------|-----|
| Register | `POST /api/auth/register/` | anyone |
| Login / refresh | `POST /api/auth/token/`, `/api/auth/token/refresh/` | anyone |
| Profile | `GET/PUT/PATCH /api/auth/profile/` | self |
| Categories | `/api/categories/` | read: all · write: manager/admin |
| Browse resources | `/api/resources/` | all (available only) |
| Manage resources | `/api/manage/resources/` (+ `activate`/`deactivate`) | manager/admin |
| Bookings | `/api/bookings/` (+ `cancel`) | employees (own) |
| Approvals | `/api/approvals/bookings/` (+ `approve`/`reject`) | manager/admin |

---

## Tests

Unit tests (model constraints) and integration tests (RBAC, booking lifecycle,
availability, FIFO approval, auth) run against PostgreSQL:

```bash
cd InventoryManagement
python manage.py test Inventory
```

---

## Deliverables checklist
- [x] Source code (this repository)
- [x] README with setup, dependencies, env config, run instructions
- [x] API documentation (Swagger UI / OpenAPI schema)
- [x] `.env.example`
- [x] Clearly defined database models and relationships
- [x] Automated tests covering the main functionality
