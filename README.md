# Attendora

**A Multi-School Smart Attendance & Student Management System**

Attendora is a full-stack school attendance and student management platform designed for centralized administration, daily attendance tracking, academic-year management, configurable student records, reporting, exports, auditability, and multi-school use.

## Key Features

### Multi-School Administration
- Super-admin support for creating and managing multiple schools.
- Separate school-level data and user access.
- School identity details including name, address and UDISE code.
- Configurable class lists and dashboard groups for each school.
- Configurable student record structure with **Visible** and **Required** field controls.

### Role-Based Access and Authentication
- JWT-based authentication.
- Super-admin, school-admin and teacher-oriented access flows.
- Teacher account creation and class assignment.
- Password change and administrator password-reset support.
- School-scoped access to protect data separation between schools.

### Student Directory
- Add, edit, search and filter student records.
- Bulk student import from Excel.
- Supports student fields such as:
  - Name
  - Class
  - Gender
  - Admission Number
  - PEN Number
  - Father’s Name
  - Mother’s Name
  - Date of Birth
  - Calculated Age
  - Category
  - Admission Date
- Missing optional spreadsheet values can remain empty for later manual editing.
- Student ordering by class, then girls first, boys next, and alphabetical name order.
- Category filtering and category-aware exports.
- Student Directory export to Excel and PDF.
- Classwise category student lists with category order: **SC → ST → OBC → General**.

### Attendance Management
- Classwise daily attendance entry.
- Present and absent marking.
- Attendance editing support.
- Working-day validation before attendance submission.
- Holiday-aware attendance rules.
- Class access restrictions based on teacher assignments.
- Consistent student sorting across attendance views and reports.

### Holiday and School Calendar Management
- Maintain school calendar days and holidays.
- Prevent attendance marking on declared holidays.
- Holiday status is reflected in dashboards and reports.
- Holidays are excluded from working-day attendance calculations and percentage calculations.

### Dashboard Analytics
- Daily attendance status and completion tracking.
- Boys present, girls present, absent and total-student statistics.
- Configurable grouped summaries, including school-defined class groups.
- Grand total summary across all configured groups.
- Holiday visibility directly on the dashboard.

### Reports and Analytics
- Daily attendance reports.
- Monthly student-wise attendance reports.
- Yearly student-wise attendance reports.
- Attendance summaries and detailed records.
- Date-wise attendance registers.
- Attendance percentage calculations based on valid working days.
- Search and class filtering for student-wise reports.

### Excel and PDF Exports
- Daily report export to Excel and PDF.
- Monthly report export to Excel and PDF.
- Yearly report export to Excel and PDF.
- Student Directory export to Excel and PDF.
- School information included in generated reports.
- Holiday information represented in report outputs.
- Structured worksheets for attendance summaries, student-wise attendance and date-wise registers.
- Readable column widths and organized student sorting.

### Academic Year Management
- Create and activate academic years.
- Promote students to the next academic year.
- Highest-class completion handling.
- Revert the last promotion if a promotion is performed accidentally.
- Historical attendance remains associated with its original records.

### Audit Log and Maintenance
- Audit logging for important system operations.
- User-readable activity history.
- India-oriented timestamp display support.
- Administrative controls for clearing audit history.
- Administrative controls for clearing attendance history when explicitly confirmed.

### Backup Support
- Backup status and manual backup endpoints.
- Designed for a shared PostgreSQL production database.
- Supports PostgreSQL backup workflows when the deployment environment provides the required database tools.

## Tech Stack

### Frontend
- **React 18**
- **Vite 6**
- **React Router DOM**
- **Axios**
- **Recharts**
- Responsive CSS-based interface

### Backend
- **Python**
- **FastAPI**
- **Uvicorn**
- **SQLAlchemy**
- **Pydantic**
- **python-jose** for JWT handling
- **python-multipart** for file uploads

### Database
- **PostgreSQL**
- **psycopg 3**

### Data Processing and Export
- **Pandas**
- **OpenPyXL**
- **xlrd**
- **ReportLab**

## Project Structure

```text
school-attendance-system/
├── backend/
│   ├── app/
│   │   ├── routers/
│   │   ├── database.py
│   │   ├── main.py
│   │   ├── models.py
│   │   └── schemas.py
│   ├── requirements.txt
│   └── migration/import utilities
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   ├── api.js
│   │   ├── App.jsx
│   │   └── main.jsx
│   ├── package.json
│   └── index.html
├── .env.example
├── .gitignore
└── README.md
```

## Local Development

### 1. Backend

```bash
cd backend
python -m venv venv
```

Activate the virtual environment.

**Windows PowerShell:**

```powershell
venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Configure the required environment variables, then run:

```bash
python -m uvicorn app.main:app --reload
```

The backend runs locally on port `8000` by default.

### 2. Frontend

Open another terminal:

```bash
cd frontend
npm install
npm run dev
```

The Vite development server normally runs on port `5173`.

## Environment Variables

Use environment variables for credentials and deployment-specific settings. Do not commit a real `.env` file.

Example backend variables:

```env
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME
CORS_ORIGINS=http://localhost:5173
```

Example frontend production variable:

```env
VITE_API_URL=https://your-backend-domain.example
```

Keep `.env.example` free of real passwords, tokens and production secrets.

## Production Architecture

A typical deployment uses:

```text
React/Vite Frontend
        │
        ▼
FastAPI REST API
        │
        ▼
PostgreSQL Database
```

All authorized school users connect to one deployed backend, while the backend communicates with the shared production PostgreSQL database.

## Deployed Applications

Attendora is deployed as a full-stack web application with separate frontend, backend API and cloud PostgreSQL database services.

### Frontend Application

The production frontend is deployed using Vercel.

**Live Application:**

https://attendora-school-management-system.vercel.app/

### Backend API

The FastAPI backend is deployed using Hugging Face Spaces and serves the REST API used by the frontend application.

**Backend Deployment:**

https://huggingface.co/spaces/Shreyashu123/attendora-backend

### Production Database

The application uses a cloud-hosted PostgreSQL database provided by Neon.

The database securely stores school configurations, users, student records, attendance data, academic-year information, holidays and other application data.

> Database credentials and connection details are intentionally not included in this repository for security reasons.

## Final Deployed Application

The complete Attendora School Management System can be accessed here:

### Attendora School Management System

https://attendora-school-management-system.vercel.app/

The deployed application connects the React frontend, FastAPI backend and PostgreSQL production database to provide a centralized multi-school attendance and student management platform.

## Recommended Pre-Deployment Checks

Before real-world use, verify authentication, school-level data isolation, all student import fields, duplicate-name student handling, attendance submission and editing, holiday behavior, percentage calculations, report exports, academic-year promotion and revert, teacher class assignments, audit timestamps, mobile responsiveness, CORS configuration, database backups and backup restoration.

## Security Notes

- Never commit `.env` files or production credentials.
- Change all default or temporary passwords before production use.
- Use HTTPS for the deployed frontend and backend.
- Restrict CORS to the actual frontend domain.
- Maintain independent database backups.
- Test restore procedures before relying on backups.
- Review authentication and authorization before handling production student data.

## Future Enhancements

Possible future additions include parent notifications, biometric or QR-assisted attendance, richer analytics, configurable attendance alerts, automated backup delivery, and dedicated mobile applications.

## License and Copyright

Copyright © 2026 **S J SHREYA**. All rights reserved.

This repository is proprietary unless a separate license file states otherwise. Replace `[Your Name]` with the legal name you want displayed before publishing the repository.

---

Built to simplify school attendance, student records, reporting and academic administration through one centralized platform.
