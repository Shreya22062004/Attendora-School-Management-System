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

function IDCard({ student, settings, photoSrc, signatureSrc, useProtectedImages = false, refreshKey }) {
  const date = value => value ? value.split("-").reverse().join("/") : "";
  const photo = student.photo_uploaded
    ? (useProtectedImages
      ? <ProtectedImage key={`${student.id}-${refreshKey}`} path={`/idcards/students/${student.id}/photo`} className="idcard-preview-photo-image" alt={`${student.name} photo`} />
      : photoSrc ? <img className="idcard-preview-photo-image" src={photoSrc} alt={`${student.name} photo`} /> : <span className="id-image-placeholder">PHOTO PENDING</span>)
    : <span className="id-image-placeholder">PHOTO PENDING</span>;

  return <article className="idcard-preview">
    <div className="idcard-preview-header">
      <div className="idcard-preview-school">{settings.school_name || "SCHOOL NAME"}</div>
      {settings.established_year && <div className="idcard-preview-estd">ESTD - {settings.established_year}</div>}
      {settings.address && <div className="idcard-preview-address">{settings.address}</div>}
      {settings.udise_code && <div className="idcard-preview-udise">UDISE CODE - {settings.udise_code}</div>}
    </div>
    <div className="idcard-preview-wave" />
    <div className="idcard-preview-photo">{photo}</div>
    <div className="idcard-preview-name">{student.name}</div>
    <div className={`idcard-preview-info${student.contact_number || student.blood_group ? " has-extra-details" : ""}`}>
      <div><b>FATHER NAME :</b><span>{student.father_name || "-"}</span></div>
      <div><b>MOTHER NAME :</b><span>{student.mother_name || "-"}</span></div>
      {student.contact_number && <div><b>CONTACT NO :</b><span>{student.contact_number}</span></div>}
      {student.blood_group && <div><b>BLOOD GROUP :</b><span>{student.blood_group}</span></div>}
      {student.pen_number && <div><b>PEN NUMBER :</b><span>{student.pen_number}</span></div>}
      {student.date_of_birth && <div><b>DATE OF BIRTH :</b><span>{date(student.date_of_birth)}</span></div>}
    </div>
    <div className="idcard-preview-signature">
      {settings.has_headmaster_signature && (useProtectedImages
        ? <ProtectedImage key={`signature-${refreshKey}`} path="/idcards/settings/signature" className="idcard-preview-signature-image" alt="Headmaster signature" />
        : signatureSrc && <img className="idcard-preview-signature-image" src={signatureSrc} alt="Headmaster signature" />)}
      <span className="idcard-preview-signature-line" />
      <b>HEADMASTER</b>
    </div>
  </article>;
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
  const [printJob, setPrintJob] = useState(null);

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

  const printCards = async studentsToPrint => {
    if (!studentsToPrint.length) return;
    setBusy("download"); setMessage("");
    try {
      const toDataUrl = async path => {
        const response = await api.get(path, { responseType: "blob" });
        return new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result);
          reader.onerror = reject;
          reader.readAsDataURL(response.data);
        });
      };
      const photoEntries = await Promise.all(studentsToPrint.map(async student => [
        student.id,
        student.photo_uploaded ? await toDataUrl(`/idcards/students/${student.id}/photo`) : ""
      ]));
      const signatureSrc = settings.has_headmaster_signature ? await toDataUrl("/idcards/settings/signature") : "";
      const pages = Array.from({ length: Math.ceil(studentsToPrint.length / 4) }, (_, index) => studentsToPrint.slice(index * 4, index * 4 + 4));
      setPrintJob({ pages, photoSources: Object.fromEntries(photoEntries), signatureSrc });
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      if (document.fonts?.ready) await document.fonts.ready;
      const images = Array.from(document.querySelectorAll(".idcard-print-root img"));
      await Promise.all(images.map(image => image.complete ? Promise.resolve() : new Promise(resolve => {
        image.addEventListener("load", resolve, { once: true });
        image.addEventListener("error", resolve, { once: true });
      })));
      // Chrome can return from window.print() before a large multi-page PDF has
      // finished consuming the print DOM. Keep all cards mounted until its print
      // lifecycle completes; otherwise the saved bulk PDF can be empty/corrupt.
      await new Promise(resolve => {
        const completePrint = () => {
          window.removeEventListener("afterprint", completePrint);
          setTimeout(resolve, 250);
        };
        window.addEventListener("afterprint", completePrint, { once: true });
        window.print();
      });
    } catch (error) {
      setMessage(error.response?.data?.detail || "Could not prepare ID cards for printing");
    } finally {
      // Keep the hidden print sheet alive after the dialog closes. Some Windows
      // PDF drivers finish writing a large document after `afterprint` fires;
      // unmounting here can truncate the generated file.
      setBusy("");
    }
  };

  return <>
    <header className="page-head">
      <div>
        <p className="eyebrow">STUDENT IDENTITY</p>
        <h1>ID Cards</h1>
        <p className="muted">85 × 110 mm portrait card • 4 cards per A4 sheet • choose Save as PDF at Actual Size.</p>
      </div>
      <button
        className="primary-btn"
        onClick={() => printCards(shown)}
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
              85 × 110 mm portrait card. Clean school branding, large photo, readable student details, and signature.
            </p>
          </div>
          <span className="badge">{previewStudent.name}</span>
        </div>

        <div className="idcard-preview-wrap">
          <IDCard student={previewStudent} settings={settings} useProtectedImages refreshKey={refreshKey} />
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
                      <>
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
                      <label className="upload-btn">
                        {busy === `photo-${student.id}` ? "Uploading..." : "Use Camera"}
                        <input
                          type="file"
                          accept="image/png,image/jpeg"
                          capture="environment"
                          disabled={busy === `photo-${student.id}`}
                          onChange={event => {
                            uploadPhoto(student, event.target.files?.[0]);
                            event.target.value = "";
                          }}
                        />
                      </label>
                      </>
                    )}
                    <button type="button" onClick={() => printCards([student])}>
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

    {printJob && <div className="idcard-print-root">
      {printJob.pages.map((page, pageIndex) => <div className="idcard-print-page" key={pageIndex}>
        {page.map(student => <IDCard
          key={student.id}
          student={student}
          settings={settings}
          photoSrc={printJob.photoSources[student.id]}
          signatureSrc={printJob.signatureSrc}
        />)}
      </div>)}
    </div>}
  </>;
}
