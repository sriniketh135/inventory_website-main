# Industrial ERP — Inventory Management System

A local inventory management system built with FastAPI (backend) and Streamlit (frontend), featuring role-based access control, secure session management, and full inward/issue transaction tracking.

---

## Features

- Role-based access: Admin, Manager, Viewer
- Secure login with Argon2 password hashing and opaque session tokens
- Inventory stock tracking with reorder alerts
- Multi-item inward (purchase) entry with invoice support
- Issue entry with stock validation
- Supplier and specification management
- Transaction history with deleted-item name preservation

---

## Requirements

- Python 3.10+
- pip

---

## Setup Instructions

### 1. Clone or copy the project folder

```bash
cd your-project-folder
```

### 2. Create and activate a virtual environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Mac/Linux
python -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Create the `.env` file

Create a file named `.env` in the root of the project folder with the following content:

```
DATABASE_URL="postgresql://username:pswrd@localhost:5432/database_name"
ERP_API_KEY=your-long-random-secret-key
```

To generate a strong key, run this once in Python:

```python
import secrets
print(secrets.token_hex(32))
```

Both the backend and frontend read this key — they must match.

### 5. Initialize the database and create the first admin user

```bash
python setup.py
```

This creates the database tables and an initial admin account:
- **Username:** admin
- **Password:** admin123


### 6. Run the application

Open **two separate terminals**, both with the venv activated.

**Terminal 1 — Backend:**
```bash
uvicorn main:app --reload
```

**Terminal 2 — Frontend:**
```bash
streamlit run app.py
```

Then open your browser and go to:
```
http://localhost:8501
```

---

## Project Structure

```
├── app.py              # Streamlit frontend
├── main.py             # FastAPI backend
├── models.py           # SQLAlchemy database models
├── database.py         # Database connection setup
├── setup.py            # First-time database initialization script
├── requirements.txt    # Python dependencies
├── .env                # Secret keys (create this manually, do not commit)
└── README.md
```

---

## User Roles

| Feature | Admin | Manager | Viewer |
|---|---|---|---|
| Stock View | ✅ | ✅ | ✅ |
| Record Inward | ✅ | ✅ | ❌ |
| Record Issue | ✅ | ✅ | ❌ |
| View Transactions | ✅ | ✅ | ❌ |
| Add/Delete Items | ✅ | ❌ | ❌ |
| Manage Suppliers | ✅ | ❌ | ❌ |
| Manage Specs | ✅ | View only | ❌ |
| Manage Users | ✅ | ❌ | ❌ |

---

## Security Notes

- Never commit the `.env` file to version control
- Add `.env` to your `.gitignore`
- The API key protects all backend routes — keep it secret
- Session tokens are stored server-side and invalidated on logout
- Passwords are hashed with Argon2 and never stored in plain text

---

## Troubleshooting

**"No Inward Transactions Found" even though data exists**
- Make sure `load_dotenv()` is at the top of both `app.py` and `main.py`
- Check that the `ERP_API_KEY` in `.env` is identical for both
- Verify the venv is active in both terminals

**"Backend not responding"**
- Make sure the uvicorn terminal is running without errors
- Check that port 8000 is not blocked or already in use

**Can't log in after restarting**
- This is normal if "Stay Logged In" was not checked — just log in again
- If it persists, check that the database file exists and `setup.py` was run