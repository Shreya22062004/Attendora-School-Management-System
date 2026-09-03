import React, { useEffect, useMemo, useRef, useState } from "react";
import api from "../services/api";

const PHOTO_CROP_WIDTH = 800;
const PHOTO_CROP_HEIGHT = 1000;

export function PhotoAdjuster({ file, person, onCancel, onSave }) {
  const frameRef = useRef(null);
  const [source, setSource] = useState(""); const [natural, setNatural] = useState(null);
  const [frame, setFrame] = useState({ width: 0, height: 0 }); const [zoom, setZoom] = useState(1);
  const [position, setPosition] = useState({ x: 0, y: 0 }); const [drag, setDrag] = useState(null); const [saving, setSaving] = useState(false);
  useEffect(() => { const url = URL.createObjectURL(file); setSource(url); return () => URL.revokeObjectURL(url); }, [file]);
  useEffect(() => { const update = () => { const rect = frameRef.current?.getBoundingClientRect(); if (rect) setFrame({ width: rect.width, height: rect.height }); }; update(); const observer = new ResizeObserver(update); if (frameRef.current) observer.observe(frameRef.current); return () => observer.disconnect(); }, []);
  const base = natural && frame.width && frame.height ? Math.min(frame.width / natural.width, frame.height / natural.height) : 1;
  const width = natural ? natural.width * base * zoom : 0; const height = natural ? natural.height * base * zoom : 0;
  const clamp = next => ({ x: Math.max(-(frame.width + width) / 2 + 20, Math.min((frame.width + width) / 2 - 20, next.x)), y: Math.max(-(frame.height + height) / 2 + 20, Math.min((frame.height + height) / 2 - 20, next.y)) });
  const save = async () => {
    if (!natural || !frame.width) return; setSaving(true);
    try {
      const image = new Image(); await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = reject; image.src = source; });
      const canvas = document.createElement("canvas"); canvas.width = PHOTO_CROP_WIDTH; canvas.height = PHOTO_CROP_HEIGHT;
      const context = canvas.getContext("2d"); context.fillStyle = "#fff7ee"; context.fillRect(0, 0, canvas.width, canvas.height);
      const scale = canvas.width / frame.width; context.drawImage(image, (canvas.width - width * scale) / 2 + position.x * scale, (canvas.height - height * scale) / 2 + position.y * scale, width * scale, height * scale);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", .95)); if (!blob) throw new Error("Photo could not be prepared");
      await onSave(new File([blob], `${person.name || "photo"}-id-card.jpg`, { type: "image/jpeg" }));
    } finally { setSaving(false); }
  };
  return <div className="modal-backdrop photo-adjuster-backdrop"><section className="modal-card photo-adjuster-card"><div><h2>Adjust Photo</h2><p className="muted">{person.name} - drag to position and use the slider to zoom.</p></div><div ref={frameRef} className="photo-adjuster-frame" onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); setDrag({ x: event.clientX, y: event.clientY, position }); }} onPointerMove={event => drag && setPosition(clamp({ x: drag.position.x + event.clientX - drag.x, y: drag.position.y + event.clientY - drag.y }))} onPointerUp={() => setDrag(null)}>{source && <img src={source} alt="Adjust photo" draggable="false" className="photo-adjuster-image" onLoad={event => setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} style={natural ? { width, height, left: `calc(50% + ${position.x}px)`, top: `calc(50% + ${position.y}px)` } : undefined} />}<span className="photo-adjuster-frame-label">20 x 25 mm stamp-size photo</span></div><label className="photo-adjuster-zoom">Zoom<div className="photo-adjuster-zoom-control"><button type="button" onClick={() => setZoom(value => Math.max(1, value - .1))}>-</button><input type="range" min="1" max="3" step=".01" value={zoom} onChange={event => setZoom(Number(event.target.value))} /><button type="button" onClick={() => setZoom(value => Math.min(3, value + .1))}>+</button></div></label><div className="modal-actions photo-adjuster-actions"><button type="button" onClick={() => { setZoom(1); setPosition({ x: 0, y: 0 }); }}>Reset</button><span/><button type="button" onClick={onCancel} disabled={saving}>Cancel</button><button type="button" className="primary-btn" onClick={save} disabled={!natural || saving}>{saving ? "Saving..." : "Confirm Photo"}</button></div></section></div>;
}

function ProtectedImage({ path, src: directSrc = "", alt, className }) {
  const [src, setSrc] = useState("");
  useEffect(() => { let url = ""; if (directSrc) { setSrc(directSrc); return undefined; } if (!path) return undefined; api.get(path, { responseType: "blob" }).then(response => { url = URL.createObjectURL(response.data); setSrc(url); }).catch(() => setSrc("")); return () => url && URL.revokeObjectURL(url); }, [path, directSrc]);
  return src ? <img className={className} src={src} alt={alt} /> : null;
}

function BloodGroupDrop({ value }) {
  return <div className="blood-drop-wrap" aria-label={`Blood group ${value || "not set"}`}>
    <svg className="blood-drop-svg" viewBox="0 0 100 125" aria-hidden="true"><path d="M50 4 C43 18 15 45 15 70 C15 96 31 113 50 113 C69 113 85 96 85 70 C85 45 57 18 50 4 Z" /></svg>
    <span>{value || ""}</span>
  </div>;
}

export function PersonIDCard({ person, settings, photoSrc, signatureSrc, protectedImages = false, refreshKey }) {
  const student = person.kind === "student";
  const date = value => value ? value.split("-").reverse().join("/") : "";
  const photoPath = student ? `/idcards/students/${person.id}/photo` : `/staff/${person.id}/photo`;
  const photo = protectedImages
    ? <ProtectedImage key={`${person.kind}-${person.id}-${refreshKey}`} src={person.photo_url} path={photoPath} className="portrait-card-photo-image" alt={`${person.name} photo`} />
    : photoSrc
      ? <img className="portrait-card-photo-image" src={photoSrc} alt={`${person.name} photo`} />
      : null;

  // Keep this list aligned with the original student card: there is intentionally NO class field.
  const rows = student
    ? [["FATHER'S NAME", person.father_name], ["MOTHER'S NAME", person.mother_name], ["CONTACT NO", person.contact_number], ["PEN NUMBER", person.pen_number], ["DATE OF BIRTH", date(person.date_of_birth)]]
    : [["DESIGNATION", person.designation], ["FATHER/HUSBAND", person.father_husband_name], ["LEVEL", person.level], ["MOBILE NO", person.mobile_number], ["DATE OF BIRTH", date(person.date_of_birth)]];

  return <article className="portrait-idcard">
    <header className="portrait-card-header">
      <img src="/odisha-govt-logo.png" className="odisha-logo" alt="Odisha Government"/>
      <div className="portrait-school-identity">
        <strong>{settings.school_name || ""}</strong>
        <span>{settings.address || ""}</span><span className="portrait-school-udise">UDISE CODE: {settings.udise_code || ""}</span>
      </div>
    </header>
    <div className="portrait-card-title">IDENTITY CARD</div>
    <div className="portrait-card-photo-row">
      <div className="portrait-card-photo">{photo}</div>
      <BloodGroupDrop value={person.blood_group}/>
    </div>
    <div className="portrait-card-details">
      <h3>{person.name}</h3>
      <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}:</dt><dd>{value || ""}</dd></div>)}</dl>
    </div>
    <div className="portrait-card-bottom">
      <div className="portrait-card-department">SCHOOL AND MASS EDUCATION DEPARTMENT</div>
      <div className="portrait-signature-area">
        {(protectedImages ? settings.has_headmaster_signature : signatureSrc) && (protectedImages
          ? <ProtectedImage key={`sign-${refreshKey}`} src={settings.headmaster_signature_url} path="/idcards/settings/signature" className="portrait-signature" alt="Headmaster signature"/>
          : <img className="portrait-signature" src={signatureSrc} alt="Headmaster signature"/>)}
        <span>HEADMASTER</span>
      </div>
      <div className="portrait-card-orange-curve" aria-hidden="true" />
    </div>
  </article>;
}

export default function IDCards() {
  const isAdmin = (localStorage.getItem("school_role") || "teacher") === "school_admin";
  const [students, setStudents] = useState([]), [staff, setStaff] = useState([]), [classes, setClasses] = useState([]), [settings, setSettings] = useState({});
  const [cardType, setCardType] = useState("ALL"), [classFilter, setClassFilter] = useState(""), [search, setSearch] = useState(""), [year, setYear] = useState("");
  const [message, setMessage] = useState(""), [busy, setBusy] = useState(""), [previewKey, setPreviewKey] = useState(""), [refreshKey, setRefreshKey] = useState(0), [printJob, setPrintJob] = useState(null), [photoEditor, setPhotoEditor] = useState(null);
  const load = async () => { try { const [studentResponse, staffResponse, configResponse, settingsResponse] = await Promise.all([api.get("/idcards/students"), api.get("/staff"), api.get("/settings/config"), api.get("/idcards/settings")]); setStudents((studentResponse.data.students || []).map(item => ({ ...item, kind: "student" }))); setStaff((staffResponse.data || []).map(item => ({ ...item, kind: "staff" }))); setClasses(configResponse.data.classes || []); setSettings(settingsResponse.data); setYear(settingsResponse.data.established_year || ""); } catch (error) { setMessage(error.response?.data?.detail || "Could not load ID cards"); } };
  useEffect(() => { load(); }, []);
  const people = useMemo(() => [...students, ...staff].filter(person => { const typeMatch = cardType === "ALL" || (cardType === "STUDENTS" ? person.kind === "student" : person.kind === "staff" && person.staff_type === cardType); const classMatch = person.kind !== "student" || !classFilter || person.class_name === classFilter; const term = search.trim().toLowerCase(); const searchMatch = !term || [person.name, person.contact_number, person.mobile_number, person.class_name, person.designation].some(value => String(value || "").toLowerCase().includes(term)); return typeMatch && classMatch && searchMatch; }), [students, staff, cardType, classFilter, search]);
  const preview = people.find(person => `${person.kind}-${person.id}` === previewKey) || people[0];
  const uploadSignature = async file => { if (!file) return; setBusy("signature"); try { const form = new FormData(); form.append("file", file); await api.post("/idcards/settings/signature", form); await load(); setRefreshKey(value => value + 1); } catch (error) { setMessage(error.response?.data?.detail || "Could not upload signature"); } finally { setBusy(""); } };
  const saveYear = async () => { try { await api.put("/idcards/settings", { established_year: year }); await load(); } catch (error) { setMessage(error.response?.data?.detail || "Could not save year"); } };
  const uploadStudentPhoto = async file => { const person = photoEditor.person; setBusy("photo"); try { const form = new FormData(); form.append("file", file); await api.post(`/idcards/students/${person.id}/photo`, form); setPhotoEditor(null); await load(); setRefreshKey(value => value + 1); } catch (error) { setMessage(error.response?.data?.detail || "Could not upload photo"); } finally { setBusy(""); } };
  const printCards = async selected => { if (!selected.length) return; setBusy("print"); try { const toData = async path => { const response = await api.get(path, { responseType: "blob" }); return await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(response.data); }); }; const photoEntries = await Promise.allSettled(selected.filter(person => person.photo_uploaded).map(async person => [`${person.kind}-${person.id}`, await toData(person.kind === "student" ? `/idcards/students/${person.id}/photo` : `/staff/${person.id}/photo`)])); const photos = Object.fromEntries(photoEntries.filter(result => result.status === "fulfilled").map(result => result.value)); const printable = selected; const signature = settings.has_headmaster_signature ? await toData("/idcards/settings/signature") : ""; setPrintJob({ pages: Array.from({ length: Math.ceil(printable.length / 9) }, (_, index) => printable.slice(index * 9, index * 9 + 9)), photos, signature }); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); window.print(); } catch (error) { setMessage(error.response?.data?.detail || "Could not prepare ID cards for printing"); } finally { setBusy(""); } };
  return <><header className="page-head"><div><p className="eyebrow">SCHOOL IDENTITY</p><h1>ID Cards</h1><p className="muted">54 x 86 mm portrait cards - print at Actual Size / 100%.</p></div><button className="primary-btn" disabled={busy === "print"} onClick={() => printCards(people)}>{busy === "print" ? "Preparing..." : "Print / Save PDF"}</button></header>{message && <div className="alert">{message}</div>}<section className="panel"><div className="form-grid"><label>Established Year<input value={year} disabled={!isAdmin} maxLength="4" onChange={event => setYear(event.target.value.replace(/\D/g, ""))}/></label>{isAdmin && <button type="button" className="primary-btn" onClick={saveYear}>Save Year</button>}<label>Headmaster Signature<input type="file" accept="image/png,image/jpeg" disabled={!isAdmin || busy === "signature"} onChange={event => { uploadSignature(event.target.files?.[0]); event.target.value = ""; }}/></label></div></section>{preview && <section className="panel idcard-preview-panel"><div className="panel-title-row"><div><h2>Card Preview</h2><p className="muted">The live preview and A4 print sheet use the same 54 x 86 mm portrait layout. Cards without photos are also printed with an empty photo box.</p></div><span className="badge">{preview.name}</span></div><div className="idcard-preview-wrap"><PersonIDCard person={preview} settings={settings} protectedImages refreshKey={refreshKey}/></div></section>}<section className="panel"><div className="panel-title-row"><div><h2>Student, Staff and SMC ID Cards</h2><p className="muted">All selected people are included in bulk printing; cards without photos keep an empty photo box.</p></div><div className="filters"><select value={cardType} onChange={event => setCardType(event.target.value)}><option value="ALL">All</option><option value="STUDENTS">Students</option><option value="TEACHER">Teachers</option><option value="STAFF">Staff</option><option value="SMC_MEMBER">SMC Members</option></select>{(cardType === "ALL" || cardType === "STUDENTS") && <select value={classFilter} onChange={event => setClassFilter(event.target.value)}><option value="">All Classes</option>{classes.map(item => <option key={item} value={item}>{item}</option>)}</select>}<input className="search" placeholder="Search name, mobile, class or designation" value={search} onChange={event => setSearch(event.target.value)}/></div></div><div className="table-wrap"><table><thead><tr><th>Person</th><th>Type</th><th>Class / Designation</th><th>Photo</th><th>Actions</th></tr></thead><tbody>{people.map(person => <tr key={`${person.kind}-${person.id}`}><td><strong>{person.name}</strong></td><td>{person.kind === "student" ? "Student" : person.staff_type.replace("_", " ")}</td><td>{person.kind === "student" ? `${person.class_name}${person.section ? ` / ${person.section}` : ""}` : person.designation || "-"}</td><td>{person.photo_uploaded ? "Uploaded" : "Pending"}</td><td><div className="row-actions"><button type="button" className="edit-btn" onClick={() => setPreviewKey(`${person.kind}-${person.id}`)}>Preview</button>{isAdmin && <><label className="upload-btn">{person.photo_uploaded ? "Change Photo" : "Upload Photo"}<input type="file" accept="image/png,image/jpeg" onChange={event => { const file = event.target.files?.[0]; if (file) setPhotoEditor({ person, file }); event.target.value = ""; }}/></label><label className="upload-btn">Use Camera<input type="file" accept="image/png,image/jpeg" capture="environment" onChange={event => { const file = event.target.files?.[0]; if (file) setPhotoEditor({ person, file }); event.target.value = ""; }}/></label>{person.photo_uploaded && <button type="button" className="danger-btn" onClick={async () => { try { await api.delete(person.kind === "student" ? `/idcards/students/${person.id}/photo` : `/staff/${person.id}/photo`); await load(); setRefreshKey(value => value + 1); } catch (error) { setMessage(error.response?.data?.detail || "Could not remove photo"); } }}>Remove Photo</button>}</>}<button type="button" onClick={() => printCards([person])}>Generate ID Card</button></div></td></tr>)}</tbody></table></div>{!people.length && <div className="empty">No matching people found.</div>}</section>{printJob && <div className="idcard-print-root">{printJob.pages.map((page, index) => <div className="idcard-print-page" key={index}>{page.map(person => <PersonIDCard key={`${person.kind}-${person.id}`} person={person} settings={settings} photoSrc={printJob.photos[`${person.kind}-${person.id}`]} signatureSrc={printJob.signature}/>)}</div>)}</div>}{photoEditor && <PhotoAdjuster file={photoEditor.file} person={photoEditor.person} onCancel={() => setPhotoEditor(null)} onSave={uploadStudentPhoto}/>}</>;
}
