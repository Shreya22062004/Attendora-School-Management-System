import React, {
  useEffect,
  useMemo,
  useState
} from "react";

import api from "../services/api";


const DEFAULT_CLASSES = ["UKG/KG2/PP1","1","2","3","4","5","6","7","8"];


const createBlankForm = () => ({
  name: "",
  class_name: "UKG/KG2/PP1",
  gender: "Boy",
  admission_no: "",
  pen_number: "",
  father_name: "",
  mother_name: "",
  date_of_birth: "",
  category: "",
  admission_date: ""
});


export default function Students() {
  const [classes,setClasses]=useState(DEFAULT_CLASSES);

  const [fieldConfig, setFieldConfig] = useState({});

  const [form, setForm] = useState(
    createBlankForm()
  );

  const [students, setStudents] = useState([]);

  const [search, setSearch] = useState(
    localStorage.getItem("student_search") || ""
  );

  const [message, setMessage] = useState("");

  const [importMode, setImportMode] = useState("merge");

  const [editingId, setEditingId] = useState(null);

  const [saving, setSaving] = useState(false);

  const [categoryFilter, setCategoryFilter] = useState("");

  const [classFilter, setClassFilter] = useState("");

  const importFile = async (file) => {
    if (!file) return;
    if(importMode === "replace" && !window.confirm("Replace mode will deactivate active students missing from this file. Historical attendance is preserved. Continue?")) return;
    const fd=new FormData(); fd.append("file",file);
    try { const r=await api.post(`/students/import?mode=${importMode}`,fd,{headers:{"Content-Type":"multipart/form-data"}});setMessage(r.data.message);load(); }
    catch(e){const d=e.response?.data?.detail;setMessage(typeof d === "string" ? d : (d?.message || "Import failed"));}
  };

  const load = async () => {

    try {

      const response = await api.get("/students");

      setStudents(response.data);

    } catch (error) {

      setMessage("Could not load students");
    }
  };

  const isVisible = (fieldName) => {
  return fieldConfig[fieldName]?.visible !== false;
  };

  const isRequired = (fieldName) => {
  return fieldConfig[fieldName]?.required === true;
  };

  useEffect(() => {
  load();

  api.get("/settings/config")
    .then((response) => {
      const config = response.data;

      if (config.classes?.length) {
        setClasses(config.classes);

        setForm((current) => ({
          ...current,
          class_name: config.classes.includes(current.class_name)
            ? current.class_name
            : config.classes[0]
        }));
      }

      setFieldConfig(config.fields || {});
    })
    .catch(() => {});
}, []);

  useEffect(() => {

    localStorage.setItem(
      "student_search",
      search
    );

  }, [search]);


  const getErrorMessage = (error) => {

    const detail = error.response?.data?.detail;


    if (Array.isArray(detail)) {

      return detail
        .map((item) => {

          if (typeof item === "string") {
            return item;
          }

          return item.msg || "Validation error";
        })
        .join(", ");
    }


    if (typeof detail === "string") {

      return detail;
    }


    if (
      detail &&
      typeof detail === "object"
    ) {

      return (
        detail.msg ||
        JSON.stringify(detail)
      );
    }


    return "Could not save student";
  };


  const submit = async (event) => {

    event.preventDefault();

    setMessage("");

    setSaving(true);


    const payload = {

      name: form.name.trim(),

      class_name: form.class_name,

      gender: form.gender,

      admission_no:
        form.admission_no?.trim() || null,

      pen_number:
        form.pen_number?.trim() || null,

      father_name:
        form.father_name?.trim() || null,

      mother_name:
        form.mother_name?.trim() || null,
      date_of_birth: form.date_of_birth || null,

      category:
        form.category?.trim() || null,

      admission_date:
        form.admission_date || null
    };


    try {

      const wasEditing = editingId !== null;


      if (wasEditing) {

        await api.put(
          `/students/${editingId}`,
          {
            ...payload,
            is_active: true
          }
        );

      } else {

        await api.post(
          "/students",
          payload
        );
      }


      await load();


      setEditingId(null);

      setForm(createBlankForm());


      setMessage(
        wasEditing
          ? "Student updated successfully"
          : "Student added successfully"
      );


    } catch (error) {

      setMessage(
        getErrorMessage(error)
      );

    } finally {

      setSaving(false);
    }
  };


  const edit = (student) => {

    setMessage("");

    setEditingId(student.id);


    setForm({

      name:
        student.name || "",

      class_name:
        student.class_name ||
        "UKG/KG2/PP1",

      gender:
        student.gender || "Boy",

      admission_no:
        student.admission_no || "",

      pen_number:
        student.pen_number || "",

      father_name:
        student.father_name || "",

      mother_name:
        student.mother_name || "",
      date_of_birth: student.date_of_birth || "",

      category:
        student.category || "",

      admission_date:
        student.admission_date
          ? String(student.admission_date).slice(0, 10)
          : ""
    });


    window.scrollTo({
      top: 0,
      behavior: "smooth"
    });
  };


  const cancelEdit = () => {

    setEditingId(null);

    setForm(createBlankForm());

    setMessage("");
  };


  const removeStudent = async (student) => {

    const confirmed = window.confirm(
      `Remove ${student.name} from active students?`
    );


    if (!confirmed) {
      return;
    }


    setMessage("");


    try {

      await api.delete(
        `/students/${student.id}`
      );


      await load();


      setMessage(
        "Student removed successfully"
      );


    } catch (error) {

      setMessage(
        getErrorMessage(error)
      );
    }
  };


  const categories = useMemo(() => {
    const counts = {};
    students.forEach((student) => {
      const category = (student.category || "Unspecified").trim().toUpperCase() || "Unspecified";
      counts[category] = (counts[category] || 0) + 1;
    });
    return Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)).map(([category, count]) => ({ category, count }));
  }, [students]);

  const download = async (path, filename) => {
    try {
      const response = await api.get(path, { responseType: "blob" });
      const url = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url; link.download = filename; document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    } catch { setMessage("Export failed"); }
  };

  const shown = useMemo(() => {

    const term = search
      .trim()
      .toLowerCase();


    return students.filter((student) => {

      const category =
        (
          student.category ||
          "Unspecified"
        )
          .trim()
          .toUpperCase();


      const categoryMatch =
        !categoryFilter ||
        category === categoryFilter;


      const classMatch =
        !classFilter ||
        student.class_name === classFilter;


      const searchMatch =
        !term ||

        student.name
          .toLowerCase()
          .includes(term) ||

        (
          student.pen_number ||
          ""
        )
          .toLowerCase()
          .includes(term) ||

        (
          student.admission_no ||
          ""
        )
          .toLowerCase()
          .includes(term) ||

        category
          .toLowerCase()
          .includes(term);


      return (
        categoryMatch &&
        classMatch &&
        searchMatch
      );

    });

  }, [
    students,
    search,
    categoryFilter,
    classFilter
  ]);


  return (
    <>

      <header className="page-head">

        <div>

          <p className="eyebrow">
            STUDENT MASTER
          </p>

          <h1>
            Students
          </h1>

          <p className="muted">
            Add, edit, search and safely remove students.
          </p>

        </div>


        <div className="count-pill">

          {students.length} active students

        </div>

      </header>


      <section className="panel"><h2>Bulk Student Import</h2><p className="muted"><b>Merge New Admissions</b> keeps all existing promoted students and adds/updates only students in the uploaded file. Use this after yearly promotion when the spreadsheet contains only new UKG admissions. <b>Replace Full Directory</b> is for a complete school roster and deactivates students missing from that file. Genuine students with the same name are preserved as separate rows.</p><div className="form-grid"><label>Import Mode<select value={importMode} onChange={e=>setImportMode(e.target.value)}><option value="merge">Merge New Admissions (recommended)</option><option value="replace">Replace Full Directory</option></select></label><label>Student File<input type="file" accept=".xlsx,.xls,.csv" onChange={e=>importFile(e.target.files?.[0])}/></label></div><p className="muted">Required columns: Student Name/Name, Class, Gender. Optional: Section, Stream, Admission No, PEN Number, Father's Name, Mother's Name, Date of Birth, Category and Admission Date. Missing optional values remain empty and can be edited manually.</p></section>

      <section className="panel">

        <h2>

          {editingId
            ? "Edit Student"
            : "Add New Student"}

        </h2>


        <form
          onSubmit={submit}
          className="form-grid"
        >

          <label>

            Student name

            <input
              value={form.name}
              onChange={(event) =>
                setForm({
                  ...form,
                  name: event.target.value
                })
              }
              required
            />

          </label>


          <label>

            Class

            <select
              value={form.class_name}
              onChange={(event) =>
                setForm({
                  ...form,
                  class_name:
                    event.target.value
                })
              }
            >

              {classes.map((className) => (

                <option
                  key={className}
                  value={className}
                >

                  {className}

                </option>
              ))}

            </select>

          </label>


          <label>

            Gender

            <select
              value={form.gender}
              onChange={(event) =>
                setForm({
                  ...form,
                  gender:
                    event.target.value
                })
              }
            >

              <option value="Boy">
                Boy
              </option>

              <option value="Girl">
                Girl
              </option>

            </select>

          </label>


          {isVisible("admission_no") && (
            <label>
              Admission No.
              <input
                value={form.admission_no}
                required={isRequired("admission_no")}
                onChange={(event) =>
                  setForm({ ...form, admission_no: event.target.value })
                }
              />
            </label>
          )}

          {isVisible("pen_number") && (
            <label>
              PEN Number
              <input
                value={form.pen_number}
                required={isRequired("pen_number")}
                onChange={(event) =>
                  setForm({ ...form, pen_number: event.target.value })
                }
              />
            </label>
          )}

          {isVisible("father_name") && (
            <label>
              Father's Name
              <input
                value={form.father_name}
                required={isRequired("father_name")}
                onChange={(event) =>
                  setForm({ ...form, father_name: event.target.value })
                }
              />
            </label>
          )}

          {isVisible("mother_name") && (
            <label>
              Mother's Name
              <input
                value={form.mother_name}
                required={isRequired("mother_name")}
                onChange={(event) =>
                  setForm({ ...form, mother_name: event.target.value })
                }
              />
            </label>
          )}

          {isVisible("date_of_birth") && (
            <label>
              Date of Birth
              <input
                type="date"
                value={form.date_of_birth}
                required={isRequired("date_of_birth")}
                onChange={(event) =>
                  setForm({ ...form, date_of_birth: event.target.value })
                }
              />
            </label>
          )}

          {isVisible("category") && (
            <label>
              Category
              <input
                placeholder="e.g. SC / ST / OBC / General"
                value={form.category}
                required={isRequired("category")}
                onChange={(event) =>
                  setForm({ ...form, category: event.target.value })
                }
              />
            </label>
          )}

          {isVisible("admission_date") && (
            <label>
              Admission Date
              <input
                type="date"
                value={form.admission_date}
                required={isRequired("admission_date")}
                onChange={(event) =>
                  setForm({ ...form, admission_date: event.target.value })
                }
              />
            </label>
          )}


          <button
            className="primary-btn"
            type="submit"
            disabled={saving}
          >

            {saving
              ? "Saving..."
              : editingId
                ? "Save Changes"
                : "+ Add Student"}

          </button>


          {editingId && (

            <button
              type="button"
              onClick={cancelEdit}
              disabled={saving}
            >

              Cancel

            </button>
          )}

        </form>


        {message && (

          <div className="alert">

            {message}

          </div>
        )}

      </section>


      <section className="panel category-section">
        <div className="panel-title-row">
          <div><h2>Category Summary</h2><p className="muted">Active student strength by category.</p></div>
          <div className="toolbar"><button onClick={() => download("/exports/students.pdf", "student-directory.pdf")}>Download PDF</button><button onClick={() => download("/exports/students.xlsx", "student-directory.xlsx")}>Download Excel</button></div>
        </div>
        <div className="category-cards">
          <button className={!categoryFilter ? "category-card selected" : "category-card"} onClick={() => setCategoryFilter("")}><span>All Students</span><strong>{students.length}</strong></button>
          {categories.map(item => <button key={item.category} className={categoryFilter === item.category ? "category-card selected" : "category-card"} onClick={() => setCategoryFilter(item.category)}><span>{item.category}</span><strong>{item.count}</strong></button>)}
        </div>
      </section>

      <section className="panel">

        <div className="panel-title-row">

          <div><h2>Student Directory</h2><p className="muted">Filter by category or search student records.</p></div>


          <div className="filters">

            <select
              value={classFilter}
              onChange={(event) =>
                setClassFilter(
                  event.target.value
                )
              }
            >

              <option value="">
                All Classes
              </option>

              {classes.map(
                (className) => (

                  <option
                    key={className}
                    value={className}
                  >

                    {
                      className ===
                        "UKG/KG2/PP1"
                        ? className
                        : `Class ${className}`
                    }

                  </option>

                )
              )}

            </select>


            <select
              value={categoryFilter}
              onChange={(event) =>
                setCategoryFilter(
                  event.target.value
                )
              }
            >

              <option value="">
                All Categories
              </option>

              {categories.map(
                (item) => (

                  <option
                    key={item.category}
                    value={item.category}
                  >

                    {item.category}
                    {" "}
                    ({item.count})

                  </option>

                )
              )}

            </select>


            <input

              className="search"

              placeholder="⌕ Search name, PEN, category or admission no."

              value={search}

              onChange={(event) =>
                setSearch(
                  event.target.value
                )
              }

            />

          </div>
        </div>


        <div className="table-wrap">

          <table>

            <thead>

              <tr>
                <th>Name</th>
                <th>Class</th>
                <th>Gender</th>

                {isVisible("admission_no") && <th>Admission No.</th>}
                {isVisible("pen_number") && <th>PEN Number</th>}
                {isVisible("father_name") && <th>Father's Name</th>}
                {isVisible("mother_name") && <th>Mother's Name</th>}

                {isVisible("date_of_birth") && (
                  <>
                    <th>Date of Birth</th>
                    <th>Age as of 1 Sept</th>
                  </>
                )}

                {isVisible("category") && <th>Category</th>}
                {isVisible("admission_date") && <th>Admission Date</th>}

                <th>Actions</th>
              </tr>

            </thead>


            <tbody>

              {shown.map((student) => (

                <tr key={student.id}>

                  <td>

                    <strong>
                      {student.name}
                    </strong>

                  </td>


                  <td>

                    <span className="badge">

                      {student.class_name}

                    </span>

                  </td>


                  <td>
                    {student.gender}
                  </td>

                  {isVisible("admission_no") && (
                    <td>{student.admission_no || "—"}</td>
                  )}

                  {isVisible("pen_number") && (
                    <td>{student.pen_number || "—"}</td>
                  )}

                  {isVisible("father_name") && (
                    <td>{student.father_name || "—"}</td>
                  )}

                  {isVisible("mother_name") && (
                    <td>{student.mother_name || "—"}</td>
                  )}

                  {isVisible("date_of_birth") && (
                    <>
                      <td>{student.date_of_birth || "—"}</td>
                      <td>{student.age_as_of_september_1 ?? "—"}</td>
                    </>
                  )}

                  {isVisible("category") && (
                    <td>{student.category || "—"}</td>
                  )}

                  {isVisible("admission_date") && (
                    <td>{student.admission_date || "—"}</td>
                  )}


                  <td>

                    <div className="row-actions">

                      <button
                        type="button"
                        className="edit-btn"
                        onClick={() =>
                          edit(student)
                        }
                      >

                        Edit

                      </button>


                      <button
                        type="button"
                        className="danger-btn"
                        onClick={() =>
                          removeStudent(student)
                        }
                      >

                        Remove

                      </button>

                    </div>

                  </td>

                </tr>
              ))}

            </tbody>

          </table>

        </div>


        {!shown.length && (

          <div className="empty">

            No students found.

          </div>
        )}

      </section>

    </>
  );
}