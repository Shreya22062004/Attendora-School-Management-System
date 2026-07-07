import React,{useEffect,useState} from "react";import {Routes,Route,NavLink,Navigate} from "react-router-dom";import Dashboard from "./pages/Dashboard";import TakeAttendance from "./pages/TakeAttendance";import Students from "./pages/Students";import Reports from "./pages/Reports";import Login from "./pages/Login";import SchoolHeader from "./components/SchoolHeader";import Admin from "./pages/Admin";import SchoolManagement from "./pages/SchoolManagement";
export default function App(){const [user,setUser]=useState(localStorage.getItem("school_user"));useEffect(()=>{const out=()=>setUser(null);window.addEventListener("auth-expired",out);return()=>window.removeEventListener("auth-expired",out)},[]);if(!user)return <Login onLogin={setUser}/>;const role=localStorage.getItem("school_role")||"teacher",isSuper=role==="super_admin";const nav=({isActive})=>isActive?"nav-link active":"nav-link";const logout=()=>{["school_token","school_user","school_role","school_name","school_info"].forEach(k=>localStorage.removeItem(k));setUser(null)};return <div className="app-shell"><aside className="sidebar">
    <div className="brand">
  <img
    src="/attendora-logo.png"
    alt="Attendora Logo"
    className="brand-logo"
  />

  <div>
    <strong>Attendora</strong>
    <span>
      {isSuper
        ? "System administration"
        : "Smart Attendance & School Management"}
    </span>
  </div>
</div>
    <nav>{!isSuper&&<><NavLink className={nav} to="/" end>▦ <span>Dashboard</span></NavLink><NavLink className={nav} to="/attendance">✓ <span>Take Attendance</span></NavLink><NavLink className={nav} to="/students">♟ <span>Students</span></NavLink><NavLink className={nav} to="/reports">▤ <span>Reports</span></NavLink></>}{!isSuper&&<NavLink className={nav} to="/management">▣ <span>Academic & Access</span></NavLink>}<NavLink className={nav} to="/admin">⚙ <span>Administration</span></NavLink></nav><div className="side-note">Signed in as {user}<br/>Role: {role}<br/><button className="logout-btn" onClick={logout}>Log out</button></div></aside><main className="main-content">{!isSuper&&<SchoolHeader/>}<Routes>{isSuper?<><Route path="/admin" element={<Admin/>}/><Route path="*" element={<Navigate to="/admin" replace/>}/></>:<><Route path="/" element={<Dashboard/>}/><Route path="/attendance" element={<TakeAttendance/>}/><Route path="/students" element={<Students/>}/><Route path="/reports" element={<Reports/>}/><Route path="/admin" element={<Admin/>}/><Route path="/management" element={<SchoolManagement/>}/><Route path="*" element={<Dashboard/>}/></>}</Routes><footer className="app-footer">© {new Date().getFullYear()} S J SHREYA. All rights reserved.</footer></main></div>}
