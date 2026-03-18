# **LeankUp Backend - Complete Setup Guide**

Welcome to the LeankUp backend! This guide will help you set up and run the backend server, even if you're new to Django.

## **What is LeankUp?**

LeankUp is a platform that combines:

- **Local Outsourcing** - Post and apply for local tasks
- **Micro-fundraising** - Create campaigns and collect donations
- **Escrow System** - Money is held safely until campaigns end
- **Wallet & Payments** - Users can withdraw funds to their bank

---

## **Prerequisites**

Before starting, make sure you have these installed:

1. **Python 3.8+** - [Download here](https://www.python.org/downloads/)
   - Check version: `python --version`

2. **PostgreSQL** - [Download here](https://www.postgresql.org/download/)
   - We'll use this as our database

3. **Git** - [Download here](https://git-scm.com/downloads)
   - For cloning the repository

4. **Postman** (optional but recommended) - [Download here](https://www.postman.com/downloads/)
   - For testing API endpoints

5. **Code Editor** - VS Code, PyCharm, or any editor you prefer

---

## **Quick Start (5 minutes)**

Open your terminal/command prompt and run these commands:

```bash
# 1. Clone the repository
git clone https://github.com/its-jedu/leankup-backend.git
cd leankup-backend

# 2. Create virtual environment
python -m venv venv

# 3. Activate virtual environment
# On Windows:
venv\Scripts\activate
# On Mac/Linux:
source venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Create PostgreSQL database
# Open PostgreSQL and run:
# CREATE DATABASE leankup_db;
# CREATE USER leankup_user WITH PASSWORD 'your_password';
# GRANT ALL PRIVILEGES ON DATABASE leankup_db TO leankup_user;

# 6. Create .env file (copy from .env.example)
cp .env.example .env
# Edit .env with your database password

# 7. Run migrations
python manage.py migrate

# 8. Create superuser (admin)
python manage.py createsuperuser

# 9. Start the server
python manage.py runserver
```

Your server is now running at **http://localhost:8000**! 🎉

---

## **Detailed Setup Guide**

### **Step 1: Get the Code**

```bash
# Clone the repository
git clone https://github.com/its-jedu/leankup-backend.git
cd leankup-backend
```

### **Step 2: Set Up Virtual Environment**

A virtual environment keeps your project dependencies separate.

```bash
# Create virtual environment
python -m venv venv

# Activate it (do this every time you work on the project)
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate

# You should see (venv) in your terminal
```

### **Step 3: Install Dependencies**

```bash
# Install all required packages
pip install -r requirements.txt

# This installs:
# - Django (web framework)
# - Django REST Framework (for APIs)
# - PostgreSQL driver
# - JWT authentication
# - Paystack integration
# - And more...
```

### **Step 4: Set Up PostgreSQL**

**Option A: Using pgAdmin (Graphical Interface)**

1. Open pgAdmin
2. Right-click on "Databases" → "Create" → "Database"
3. Name it `leankup_db`
4. Create a user: Right-click on "Login/Group Roles" → "Create" → "Login/Group Role"
5. Name it `leankup_user`, set password, and give it "Can login" privilege

**Option B: Using Command Line (RECOMMENDED)**

```sql
-- Open PostgreSQL prompt: psql -U postgres
CREATE DATABASE leankup_db;
CREATE USER leankup_user WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE leankup_db TO leankup_user;
\q
```

### **Step 5: Configure Environment Variables**

Create a `.env` file in the root directory:

```env
# Django Settings
SECRET_KEY=your-secret-key-here-change-in-production
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database Settings
DB_NAME=leankup_db
DB_USER=leankup_user
DB_PASSWORD=your_secure_password  # Use the password you set in Step 4
DB_HOST=localhost
DB_PORT=5432

# CORS Settings (for frontend)
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8000

# Paystack Keys (for payments)
PAYSTACK_SECRET_KEY=your_paystack_secret_key
PAYSTACK_PUBLIC_KEY=your_paystack_public_key
```

### **Step 6: Create Database Tables**

```bash
# Create and apply migrations
python manage.py makemigrations
python manage.py migrate
```

### **Step 7: Create Admin User**

```bash
# Create superuser for admin panel
python manage.py createsuperuser

# Follow prompts:
# Username: admin
# Email: admin@example.com
# Password: Admin@123456 (or your own secure password)
```

### **Step 8: Run the Server**

```bash
# Start development server
python manage.py runserver

# You should see:
# Starting development server at http://127.0.0.1:8000/
```

### **Step 9: Test It's Working**

Open your browser and visit:

- **API Root**: http://localhost:8000/api/
- **Admin Panel**: http://localhost:8000/admin/ (use admin credentials)

---

## **Understanding the Project Structure**

```
leankup-backend/
├── manage.py                 # Django's command-line tool
├── requirements.txt          # All Python dependencies
├── .env                      # Your environment variables (create this)
├── .env.example              # Example environment variables
├── .gitignore                # Files Git should ignore
│
├── config/                   # Project configuration
│   ├── settings.py           # All Django settings
│   ├── urls.py              # Main URL configuration
│   └── wsgi.py/asgi.py      # Server configuration
│
└── apps/                     # All application modules
    ├── auth/                 # Authentication
    │   ├── views.py          # Login, register, logout
    │   ├── serializers.py    # Data validation
    │   └── urls.py           # Auth endpoints
    │
    ├── users/                # User profiles
    │   ├── models.py         # Profile model
    │   ├── views.py          # Profile management
    │   └── urls.py
    │
    ├── outsourcing/          # Tasks
    │   ├── models.py         # Task and Application models
    │   ├── views.py          # Task CRUD operations
    │   └── urls.py
    │
    ├── fundraising/          # Campaigns & Escrow
    │   ├── models.py         # Campaign and Contribution models
    │   ├── views.py          # Campaign management, escrow
    │   └── urls.py
    │
    ├── wallet/               # Wallet
    │   ├── models.py         # Wallet and Transaction models
    │   ├── views.py          # Balance, withdrawals
    │   └── urls.py
    │
    └── payments/             # Paystack Integration
        ├── services.py       # Paystack API calls
        ├── models.py         # Payment records
        ├── views.py          # Payment endpoints
        └── urls.py
```

---

## **API Endpoints Overview**

Here are the main endpoints you'll use:

### **Authentication**

```
POST   /api/auth/register/     - Create new account
POST   /api/auth/login/        - Login (get token)
POST   /api/auth/logout/       - Logout
POST   /api/auth/token/refresh/ - Refresh expired token
```

### **User Profile**

```
GET    /api/users/me/          - Get your profile
PUT    /api/users/me/          - Update your profile
```

### **Tasks**

```
GET    /api/tasks/             - List all tasks
POST   /api/tasks/             - Create a task
GET    /api/tasks/{id}/        - View task details
POST   /api/tasks/{id}/apply/  - Apply to task
```

### **Campaigns**

```
GET    /api/campaigns/         - List all campaigns
POST   /api/campaigns/         - Create campaign
POST   /api/campaigns/{id}/contribute/ - Donate
GET    /api/campaigns/my_escrow/ - View your escrow balance
POST   /api/campaigns/{id}/release_funds/ - Withdraw funds
```

### **Wallet**

```
GET    /api/wallet/balance/    - Check balance
GET    /api/wallet/transactions/ - View transaction history
POST   /api/wallet/withdraw/   - Withdraw to bank
```

---

## **Testing with Postman**

1. **Download Postman** from [postman.com](https://www.postman.com/downloads/)

2. **Import the collection** (create a new collection called "LeankUp")

3. **Set up environment variables:**
   - Click the "Environment" tab
   - Create new environment "Local"
   - Add variable: `base_url` = `http://localhost:8000`
   - Add variable: `token` (leave empty)

4. **Test Registration:**
   - Method: `POST`
   - URL: `{{base_url}}/api/auth/register/`
   - Body (raw JSON):

   ```json
   {
     "username": "testuser",
     "email": "test@example.com",
     "password": "Test@123456",
     "password2": "Test@123456",
     "first_name": "Test",
     "last_name": "User"
   }
   ```

5. **Test Login:**
   - Method: `POST`
   - URL: `{{base_url}}/api/auth/login/`
   - Body:

   ```json
   {
     "username": "testuser",
     "password": "Test@123456"
   }
   ```

   - In Tests tab, add:

   ```javascript
   const response = pm.response.json();
   pm.environment.set("token", response.access);
   ```

6. **Test Protected Endpoint:**
   - Method: `GET`
   - URL: `{{base_url}}/api/users/me/`
   - Headers: `Authorization: Bearer {{token}}`

---

## **Environment Variables Explained**

| Variable               | Purpose                            | Example                              |
| ---------------------- | ---------------------------------- | ------------------------------------ |
| `SECRET_KEY`           | Django security key (keep secret!) | `django-insecure-xyz123`             |
| `DEBUG`                | Set to `False` in production       | `True` for development               |
| `ALLOWED_HOSTS`        | Allowed domain names               | `localhost,127.0.0.1,yourdomain.com` |
| `DB_NAME`              | Database name                      | `leankup_db`                         |
| `DB_USER`              | Database username                  | `leankup_user`                       |
| `DB_PASSWORD`          | Database password                  | `SecurePass123!`                     |
| `DB_HOST`              | Database host                      | `localhost`                          |
| `DB_PORT`              | Database port                      | `5432`                               |
| `CORS_ALLOWED_ORIGINS` | Frontend URLs                      | `http://localhost:3000`              |
| `PAYSTACK_SECRET_KEY`  | Paystack secret (test/live)        | `sk_test_xxxx`                       |
| `PAYSTACK_PUBLIC_KEY`  | Paystack public key                | `pk_test_xxxx`                       |

---

## ❗ **Common Issues & Solutions**

### **Issue: "ModuleNotFoundError: No module named 'decouple'"**

**Solution:** Install missing package

```bash
pip install python-decouple
```

### **Issue: "Connection refused" for PostgreSQL**

**Solution:** Make sure PostgreSQL is running

```bash
# Windows: Check Services (search "Services" in Start)
# Find PostgreSQL and start it

# Mac/Linux:
sudo service postgresql start
```

### **Issue: "duplicate key value violates unique constraint"**

**Solution:** You're trying to create a user/email that already exists

- Use a different username/email
- Or delete the existing user from database

### **Issue: "Invalid password format"**

**Solution:** Password must have:

- At least 8 characters
- At least one letter
- At least one number
- At least one special character (!@#$%^&\*)

### **Issue: "Token invalid/expired"**

**Solution:** Get a new token by logging in again

```bash
POST /api/auth/login/
```

### **Issue: "CSRF verification failed"**

**Solution:** Make sure you're including the token in headers

```bash
Headers: Authorization: Bearer your_token_here
```

### **Issue: Database migrations not applying**

**Solution:** Reset and reapply

```bash
python manage.py migrate fundraising_app zero
python manage.py migrate
```

---

## **Running in Production**

When you're ready to deploy:

1. **Set `DEBUG=False`** in `.env`
2. **Update `ALLOWED_HOSTS`** with your domain
3. **Use a production database** (not SQLite)
4. **Set up SSL/HTTPS**
5. **Use environment variables** for all secrets
6. **Collect static files:**

```bash
python manage.py collectstatic
```

7. **Use Gunicorn/uWSGI** instead of `runserver`

---

## 🎉 **You're All Set!**
