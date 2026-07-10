import React,{useEffect,useState} from 'react';import api from '../services/api';
export default function SchoolManagement(){const [years,setYears]=useState([]),[classes,setClasses]=useState([]),[students,setStudents]=useState([]),[teachers,setTeachers]=useState([]),[logs,setLogs]=useState([]),[msg,setMsg]=useState('');const [year,setYear]=useState({name:'',start_date:'',end_date:'',is_active:true});const [teacher,setTeacher]=useState({username:'',password:'',classes:[]});const [editingTeacher,setEditingTeacher]=useState(null);const [holiday,setHoliday]=useState({start_date:'',end_date:'',day_type:'Declared Holiday',description:''});
const load=()=>{api.get('/academic/years').then(r=>setYears(r.data)).catch(e=>setMsg(e.response?.data?.detail||'Could not load academic years'));api.get('/settings/config').then(r=>setClasses(r.data.classes||[]));api.get('/students/promotion-list').then(r=>setStudents(r.data)).catch(e=>setMsg(e.response?.data?.detail||'Could not load promotion data'));api.get('/auth/teachers').then(r=>setTeachers(r.data)).catch(()=>{});api.get('/academic/audit').then(r=>setLogs(r.data)).catch(()=>{})};useEffect(load,[]);
const addYear=async e=>{e.preventDefault();try{setMsg((await api.post('/academic/years',year)).data.message);load()}catch(e){setMsg(e.response?.data?.detail||'Failed')}};

const activateYear = async (yearId) => {
  try {
    const response = await api.put(
      `/academic/years/${yearId}/activate`
    );

    setMsg(response.data.message);
    load();
  } catch (e) {
    setMsg(
      e.response?.data?.detail ||
      'Could not activate academic year'
    );
  }
};

const saveTeacher=async e=>{e.preventDefault();try{let r;if(editingTeacher)r=await api.put(`/auth/teachers/${editingTeacher}/assignments`,{...teacher,role:'teacher',password:teacher.password||'not-used'});else r=await api.post('/auth/users',{...teacher,role:'teacher'});setMsg(r.data.message);setTeacher({username:'',password:'',classes:[]});setEditingTeacher(null);load()}catch(e){setMsg(e.response?.data?.detail||'Failed')}};
const beginEdit=t=>{setEditingTeacher(t.id);setTeacher({username:t.username,password:'',classes:t.assignments.map(a=>a.class_name)})};
const resetTeacherPassword=async t=>{const newPassword=prompt(`Enter a new temporary password for ${t.username} (minimum 8 characters)`);if(newPassword===null)return;if(newPassword.length<8){setMsg('Password must be at least 8 characters');return}if(!confirm(`Reset password for ${t.username}?`))return;try{const r=await api.post(`/auth/users/${t.id}/reset-password`,{new_password:newPassword});setMsg(r.data.message)}catch(e){setMsg(e.response?.data?.detail||'Could not reset password')}};

const saveHoliday=async e=>{e.preventDefault();try{setMsg((await api.post('/calendar',{...holiday,end_date:holiday.end_date||null})).data.message);setHoliday({...holiday,start_date:'',end_date:'',description:''})}catch(e){setMsg(e.response?.data?.detail||'Failed')}};
const promote=async()=>{const sorted=[...years].sort((a,b)=>new Date(b.start_date)-new Date(a.start_date)),to=sorted[0]?.id,from=sorted[1]?.id;if(!from||!to)return setMsg('Create at least two academic years first');if(!confirm('Promote every active student using the school class order? Students in the highest configured class will be marked Completed.'))return;const promotions=students.filter(s=>s.is_active).map(s=>({student_id:s.id,section:s.section,stream:s.stream}));try{setMsg((await api.post('/academic/promote',{from_academic_year_id:from,to_academic_year_id:to,promotions})).data.message);load()}catch(e){setMsg(e.response?.data?.detail||'Promotion failed')}};
const revertPromotion=async()=>{const sorted=[...years].sort((a,b)=>new Date(b.start_date)-new Date(a.start_date)),to=sorted[0]?.id,from=sorted[1]?.id;if(!from||!to)return setMsg('Create at least two academic years first');if(!confirm('Revert the latest promotion and return students to the previous academic year? Use this only to undo an accidental promotion.'))return;try{setMsg((await api.post('/academic/promotion/revert',{from_academic_year_id:from,to_academic_year_id:to,promotions:[]})).data.message);load()}catch(e){setMsg(e.response?.data?.detail||'Revert failed')}};
return <><header className="page-head"><div><p className="eyebrow">SCHOOL OPERATIONS</p><h1>Academic & Access Management</h1></div></header>{msg&&<div className="alert">{msg}</div>}<section className="panel"><h2>Academic Years</h2><form className="form-grid" onSubmit={addYear}><label>Name<input placeholder="2026–27" value={year.name} onChange={e=>setYear({...year,name:e.target.value})} required/></label><label>Start<input type="date" value={year.start_date} onChange={e=>setYear({...year,start_date:e.target.value})} required/></label><label>End<input type="date" value={year.end_date} onChange={e=>setYear({...year,end_date:e.target.value})} required/></label><button className="primary-btn">Create Academic Year</button></form>
<div className="toolbar academic-year-buttons">
  {years.map(y => (
    <button
      key={y.id}
      type="button"
      className={
        y.is_active
          ? "year-btn year-active"
          : "year-btn"
      }
      onClick={() => {
        if (!y.is_active) {
          activateYear(y.id);
        }
      }}
      disabled={y.is_active}
    >
      {y.name}
      {y.is_active
        ? " · Active"
        : " · Make Active"}
    </button>
  ))}
</div>

<div className="toolbar"><button onClick={promote}>Promote Students to Next Academic Year</button><button onClick={revertPromotion}>Revert Last Promotion</button></div><p className="muted">Promotion follows this school's configured class order. The highest class is marked Completed and is never promoted beyond the school's limit. Historical attendance remains unchanged.</p></section>
<section className="panel"><h2>{editingTeacher?'Edit Teacher Class Assignments':'Create Teacher + Assign Classes'}</h2><form className="form-grid" onSubmit={saveTeacher}><label>Username<input value={teacher.username} disabled={!!editingTeacher} onChange={e=>setTeacher({...teacher,username:e.target.value})} required/></label>{!editingTeacher&&<label>Temporary Password<input type="password" minLength="8" value={teacher.password} onChange={e=>setTeacher({...teacher,password:e.target.value})} required/></label>}<label>Assigned Classes<select multiple value={teacher.classes} onChange={e=>setTeacher({...teacher,classes:[...e.target.selectedOptions].map(o=>o.value)})}>{classes.map(c=><option key={c}>{c}</option>)}</select></label><button className="primary-btn">{editingTeacher?'Save Assignments':'Create Teacher'}</button>{editingTeacher&&<button type="button" onClick={()=>{setEditingTeacher(null);setTeacher({username:'',password:'',classes:[]})}}>Cancel</button>}</form><div className="table-wrap"><table><thead><tr><th>Teacher</th><th>Assigned classes</th><th>Action</th></tr></thead><tbody>{teachers.map(t=><tr key={t.id}><td>{t.username}</td><td>{t.assignments.map(a=>a.class_name+(a.section?`-${a.section}`:'')).join(', ')||'None'}</td><td><button className="edit-btn" onClick={()=>beginEdit(t)}>Edit Classes</button> <button type="button" onClick={()=>resetTeacherPassword(t)}>Forgot Password</button></td></tr>)}</tbody></table></div></section>
<section className="panel"><h2>Holiday & School Calendar</h2><form className="form-grid" onSubmit={saveHoliday}><label>Start Date<input type="date" value={holiday.start_date} onChange={e=>setHoliday({...holiday,start_date:e.target.value})} required/></label><label>End Date (optional)<input type="date" min={holiday.start_date} value={holiday.end_date} onChange={e=>setHoliday({...holiday,end_date:e.target.value})}/></label><label>Type<select value={holiday.day_type} onChange={e=>setHoliday({...holiday,day_type:e.target.value})}>{['Declared Holiday','Local Holiday','Holiday List','Examination','Summer Vacation','Emergency Closure','Working Day','Sunday'].map(x=><option key={x}>{x}</option>)}</select></label><label>Description<input value={holiday.description} onChange={e=>setHoliday({...holiday,description:e.target.value})}/></label><button className="primary-btn">Save Calendar Range</button></form></section>
<section className="panel"><h2>Recent Audit Log</h2><div className="table-wrap"><table><thead><tr><th>Time</th><th>User</th><th>Role</th><th>Activity</th></tr></thead><tbody>{logs.slice(0,50).map(x=><tr key={x.id}><td>{new Date(x.created_at).toLocaleString()}</td><td><strong>{x.actor_name||'System'}</strong></td><td>{x.actor_role||'system'}</td><td>{x.description||x.action}</td></tr>)}</tbody></table></div></section><section className="panel"><h2>Data Maintenance</h2><p className="muted">Use these only when you intentionally want to remove operational history. Student directory data is not deleted by either action.</p><div className="toolbar"><button onClick={async()=>{if(prompt('Type CLEAR AUDIT LOG to confirm')!=='CLEAR AUDIT LOG')return;try{setMsg((await api.delete('/academic/maintenance/clear-audit',{params:{confirm:'CLEAR AUDIT LOG'}})).data.message);load()}catch(e){setMsg(e.response?.data?.detail||'Failed')}}}>Clear Audit Log</button><button onClick={async()=>{if(prompt('Type CLEAR ATTENDANCE HISTORY to confirm')!=='CLEAR ATTENDANCE HISTORY')return;try{setMsg((await api.delete('/academic/maintenance/clear-attendance',{params:{confirm:'CLEAR ATTENDANCE HISTORY'}})).data.message);load()}catch(e){setMsg(e.response?.data?.detail||'Failed')}}}>Clear Past Attendance</button></div></section></>}
