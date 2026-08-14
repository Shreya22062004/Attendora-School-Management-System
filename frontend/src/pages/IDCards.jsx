import React, { useEffect, useMemo, useState } from "react";
import api from "../services/api";

function ProtectedImage({ path, alt, className }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let url = "";
    if (!path) { setSrc(""); return undefined; }
    api.get(path, { responseType: "blob" }).then(response => {
      url = URL.createObjectURL(response.data);
      setSrc(url);
    }).catch(() => setSrc(""));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [path]);
  return src
    ? <img className={className} src={src} alt={alt} />
    : <span className="id-image-placeholder">No image</span>;
}

export default function IDCards() {
  const isAdmin = (localStorage.getItem("school_role") || "teacher") === "school_admin";
  const [students, setStudents] = useState([]);
  const [classes, setClasses] = useState([]);
  const [settings, setSettings] = useState({
    school_name: "",
    established_year: "",
    address: "",
    udise_code: "",
    has_headmaster_signature: false
  });
  const [year, setYear] = useState("");
  const [search, setSearch] = useState("");
  const [classFilter, setClassFilter] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [previewId, setPreviewId] = useState(null);

  const load = async () => {
    try {
      const [studentResponse, configResponse, settingsResponse] = await Promise.all([
        api.get("/idcards/students"),
        api.get("/settings/config"),
        api.get("/idcards/settings")
      ]);
      setStudents(studentResponse.data.students || []);
      setClasses(configResponse.data.classes || []);
      setSettings(settingsResponse.data);
      setYear(settingsResponse.data.established_year || "");
    } catch (error) {
      setMessage(error.response?.data?.detail || "Could not load ID card information");
    }
  };

  useEffect(() => { load(); }, []);

  const shown = useMemo(() => {
    const term = search.trim().toLowerCase();
    return students.filter(student =>
      (!classFilter || student.class_name === classFilter) &&
      (!term || student.name.toLowerCase().includes(term))
    );
  }, [students, classFilter, search]);

  const uploaded = shown.filter(student => student.photo_uploaded).length;
  const previewStudent = shown.find(student => student.id === previewId) || shown[0];
  const displayDate = value => value ? value.split("-").reverse().join("/") : "";

  const saveYear = async () => {
    setBusy("year"); setMessage("");
    try {
      await api.put("/idcards/settings", { established_year: year });
      setMessage("Established year saved.");
      await load();
    } catch (error) {
      setMessage(error.response?.data?.detail || "Could not save settings");
    } finally { setBusy(""); }
  };

  const uploadSignature = async file => {
    if (!file) return;
    setBusy("signature"); setMessage("");
    try {
      const form = new FormData();
      form.append("file", file);
      await api.post("/idcards/settings/signature", form);
      setMessage("Headmaster signature saved.");
      await load();
      setRefreshKey(key => key + 1);
    } catch (error) {
      setMessage(error.response?.data?.detail || "Could not upload signature");
    } finally { setBusy(""); }
  };

  const uploadPhoto = async (student, file) => {
    if (!file) return;
    setBusy(`photo-${student.id}`); setMessage("");
    try {
      const form = new FormData();
      form.append("file", file);
      await api.post(`/idcards/students/${student.id}/photo`, form);
      setMessage(`Photo saved for ${student.name}.`);
      await load();
      setRefreshKey(key => key + 1);
    } catch (error) {
      setMessage(error.response?.data?.detail || "Could not upload photo");
    } finally { setBusy(""); }
  };

  const download = async (path, filename) => {
    setBusy("download"); setMessage("");
    try {
      const response = await api.get(path, { responseType: "blob" });
      const url = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setMessage(error.response?.data?.detail || "PDF generation failed");
    } finally { setBusy(""); }
  };

  return <>
    <header className="page-head">
      <div>
        <p className="eyebrow">STUDENT IDENTITY</p>
        <h1>ID Cards</h1>
        <p className="muted">A3 portrait card • 92 × 115 mm • 4 cards per A4 sheet.</p>
      </div>
      <button
        className="primary-btn"
        onClick={() => download(
          `/idcards/bulk.pdf${classFilter ? `?class_name=${encodeURIComponent(classFilter)}` : ""}`,
          "attendora-id-cards.pdf"
        )}
        disabled={busy === "download"}
      >
        {busy === "download" ? "Generating..." : "Download All ID Cards"}
      </button>
    </header>

    {message && <div className="alert">{message}</div>}

    <section className="panel">
      <div className="panel-title-row">
        <div>
          <h2>School Settings / ID Card Settings</h2>
          <p className="muted">School details are applied automatically to every card.</p>
        </div>
      </div>

      <div className="form-grid">
        <label>
          Established Year
          <input
            value={year}
            maxLength="4"
            inputMode="numeric"
            placeholder="e.g. 1889"
            disabled={!isAdmin}
            onChange={event => setYear(event.target.value.replace(/\D/g, ""))}
          />
        </label>

        {isAdmin && (
          <button className="primary-btn" type="button" disabled={busy === "year"} onClick={saveYear}>
            {busy === "year" ? "Saving..." : "Save Year"}
          </button>
        )}

        <label>
          Headmaster Signature
          <input
            type="file"
            accept="image/png,image/jpeg"
            disabled={!isAdmin || busy === "signature"}
            onChange={event => {
              uploadSignature(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </label>
      </div>

      <div className="signature-preview">
        {settings.has_headmaster_signature
          ? <ProtectedImage key={refreshKey} path="/idcards/settings/signature" className="signature-image" alt="Current headmaster signature" />
          : <span className="muted">No signature uploaded.</span>}
        <span>
          {settings.has_headmaster_signature
            ? "Current signature — upload another image to replace it."
            : "Upload the signature once; it will be used on every ID card."}
        </span>
      </div>

      {!isAdmin && <p className="muted">Only the school administrator can change ID card settings or student photos.</p>}
    </section>

    {previewStudent && (
      <section className="panel idcard-preview-panel">
        <div className="panel-title-row">
          <div>
            <h2>Print Design Preview</h2>
            <p className="muted">
              A3 portrait card: 92 × 115 mm. Clean school branding, large photo, readable student details, and signature.
            </p>
          </div>
          <span className="badge">{previewStudent.name}</span>
        </div>

        <div className="idcard-preview-wrap">
          <article className="idcard-preview">
            <div className="idcard-preview-header">
              <div className="idcard-preview-school">{settings.school_name || "SCHOOL NAME"}</div>
              {settings.established_year && <div className="idcard-preview-estd">ESTD - {settings.established_year}</div>}
              {settings.address && <div className="idcard-preview-address">{settings.address}</div>}
              {settings.udise_code && <div className="idcard-preview-udise">UDISE CODE - {settings.udise_code}</div>}
            </div>

            <div className="idcard-preview-wave" />
            <div className="idcard-preview-photo">
              {previewStudent.photo_uploaded
                ? <ProtectedImage
                    key={`${previewStudent.id}-${refreshKey}`}
                    path={`/idcards/students/${previewStudent.id}/photo`}
                    className="idcard-preview-photo-image"
                    alt={`${previewStudent.name} photo`}
                  />
                : <span className="id-image-placeholder">PHOTO PENDING</span>}
            </div>

            <div className="idcard-preview-name">{previewStudent.name}</div>

            <div className="idcard-preview-info">
              <div><b>FATHER NAME :</b><span>{previewStudent.father_name || "-"}</span></div>
              <div><b>MOTHER NAME :</b><span>{previewStudent.mother_name || "-"}</span></div>
              {previewStudent.pen_number && <div><b>PEN NUMBER :</b><span>{previewStudent.pen_number}</span></div>}
              {previewStudent.date_of_birth && <div><b>DATE OF BIRTH :</b><span>{displayDate(previewStudent.date_of_birth)}</span></div>}
            </div>

            <div className="idcard-preview-signature">
              {settings.has_headmaster_signature && (
                <ProtectedImage
                  key={`signature-${refreshKey}`}
                  path="/idcards/settings/signature"
                  className="idcard-preview-signature-image"
                  alt="Headmaster signature"
                />
              )}
              <span className="idcard-preview-signature-line" />
              <b>HEADMASTER</b>
            </div>
          </article>
        </div>
      </section>
    )}

    <section className="panel">
      <div className="panel-title-row">
        <div>
          <h2>Student ID Cards</h2>
          <p className="muted">
            {shown.length} Students | {uploaded} Photos Uploaded | {shown.length - uploaded} Photos Pending
          </p>
        </div>
        <div className="filters">
          <select value={classFilter} onChange={event => setClassFilter(event.target.value)}>
            <option value="">All Classes</option>
            {classes.map(className => (
              <option key={className} value={className}>
                {className === "UKG/KG2/PP1" ? className : `Class ${className}`}
              </option>
            ))}
          </select>
          <input
            className="search"
            placeholder="Search student..."
            value={search}
            onChange={event => setSearch(event.target.value)}
          />
        </div>
      </div>

      <p className="muted">Cards with pending photos generate with a clearly marked photo placeholder.</p>

      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Student</th><th>Class</th><th>Photo</th><th>Status</th><th>Action</th></tr>
          </thead>
          <tbody>
            {shown.map(student => (
              <tr key={student.id} className={previewStudent?.id === student.id ? "idcard-selected-row" : ""}>
                <td><strong>{student.name}</strong></td>
                <td><span className="badge">{student.class_name}</span></td>
                <td>
                  <div className="student-photo-preview">
                    {student.photo_uploaded
                      ? <ProtectedImage
                          key={`${student.id}-${refreshKey}`}
                          path={`/idcards/students/${student.id}/photo`}
                          className="student-photo-image"
                          alt={`${student.name} photo`}
                        />
                      : <span className="id-image-placeholder">No photo</span>}
                  </div>
                </td>
                <td>{student.photo_uploaded ? "Uploaded" : "Not Uploaded"}</td>
                <td>
                  <div className="row-actions">
                    <button type="button" className="edit-btn" onClick={() => setPreviewId(student.id)}>Preview</button>
                    {isAdmin && (
                      <label className="upload-btn">
                        {busy === `photo-${student.id}` ? "Uploading..." : student.photo_uploaded ? "Change Photo" : "Upload Photo"}
                        <input
                          type="file"
                          accept="image/png,image/jpeg"
                          disabled={busy === `photo-${student.id}`}
                          onChange={event => {
                            uploadPhoto(student, event.target.files?.[0]);
                            event.target.value = "";
                          }}
                        />
                      </label>
                    )}
                    <button type="button" onClick={() => download(`/idcards/${student.id}.pdf`, `id-card-${student.id}.pdf`)}>
                      Generate ID Card
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!shown.length && <div className="empty">No students found for this filter.</div>}
    </section>
  </>;
}
