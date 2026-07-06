from pathlib import Path

import pandas as pd

from app.database import SessionLocal, Base, engine
from app.models import Student, School


BASE_DIR = Path(__file__).resolve().parent

EXCEL_FILE = (
    BASE_DIR
    / "data"
    / "MDM_DAILY_WITH_SCHOOL_DETAILS.xlsx"
)

SHEET_NAME = "Sheet3"


CLASS_MAPPING = {
    "UKG/KG2/PP1": "UKG/KG2/PP1",
    "I": "1",
    "II": "2",
    "III": "3",
    "IV": "4",
    "V": "5",
    "VI": "6",
    "VII": "7",
    "VIII": "8",
}


def normalize_gender(value):

    g = str(value).strip().lower()

    if g in (
        "male",
        "m",
        "boy",
        "b"
    ):
        return "Boy"

    if g in (
        "female",
        "f",
        "girl",
        "g"
    ):
        return "Girl"

    return None


def clean_text(value):

    if pd.isna(value):
        return None

    value = str(value).strip()

    if (
        value == ""
        or value.lower() == "nan"
    ):
        return None

    return value


def read_school_details():

    school_info = pd.read_excel(
        EXCEL_FILE,
        sheet_name=SHEET_NAME,
        header=None,
        nrows=3
    )

    school_name = clean_text(
        school_info.iloc[0, 0]
    )

    school_address = clean_text(
        school_info.iloc[1, 0]
    )

    udise_text = clean_text(
        school_info.iloc[2, 0]
    )

    if not school_name:
        raise ValueError(
            "School name not found in Excel row 1"
        )

    if not school_address:
        raise ValueError(
            "School address not found in Excel row 2"
        )

    if not udise_text:
        raise ValueError(
            "UDISE code not found in Excel row 3"
        )

    if ":" in udise_text:

        udise_code = (
            udise_text
            .split(":", 1)[1]
            .strip()
        )

    else:

        udise_code = udise_text.strip()

    return (
        school_name,
        school_address,
        udise_code
    )


def get_or_create_school(
    db,
    school_name,
    school_address,
    udise_code
):

    school = (
        db.query(School)
        .filter(
            School.udise_code == udise_code
        )
        .first()
    )

    if school:

        print("\nSCHOOL FOUND")
        print(
            f"School ID   : {school.id}"
        )
        print(
            f"School Name : {school.school_name}"
        )
        print(
            f"Address     : {school.address}"
        )
        print(
            f"UDISE Code  : {school.udise_code}"
        )

        return school

    print("\nSCHOOL NOT FOUND")
    print("Creating school from Excel details...")

    school = School(
        school_name=school_name,
        address=school_address,
        udise_code=udise_code,
        is_active=True
    )

    db.add(school)

    db.flush()

    print("\nNEW SCHOOL CREATED")

    print(
        f"School ID   : {school.id}"
    )

    print(
        f"School Name : {school.school_name}"
    )

    print(
        f"Address     : {school.address}"
    )

    print(
        f"UDISE Code  : {school.udise_code}"
    )

    return school


def read_students():

    df = pd.read_excel(
        EXCEL_FILE,
        sheet_name=SHEET_NAME,
        header=4
    )

    print("\nEXCEL COLUMNS FOUND")

    for column in df.columns:
        print(column)

    df = df.rename(
        columns={
            "SL.NO.": "class_raw",
            "NAME OF THE STUDENT": "name",
            "GENDER": "gender_raw"
        }
    )

    required_columns = [
        "class_raw",
        "name",
        "gender_raw"
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:

        raise ValueError(
            "Required Excel columns missing: "
            + str(missing_columns)
        )

    df = df.dropna(
        subset=[
            "class_raw",
            "name",
            "gender_raw"
        ]
    ).copy()

    df["class_raw"] = (
        df["class_raw"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["name"] = (
        df["name"]
        .astype(str)
        .str.strip()
    )

    df["class_name"] = (
        df["class_raw"]
        .map(CLASS_MAPPING)
    )

    df["gender"] = (
        df["gender_raw"]
        .apply(normalize_gender)
    )

    df = df[
        df["class_name"].notna()
        &
        df["gender"].notna()
    ].copy()

    return df


def print_classwise_extract(df):

    print(
        "\nCLASSWISE / GENDERWISE EXTRACT"
    )

    for class_name in (
        "UKG/KG2/PP1",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8"
    ):

        class_df = df[
            df["class_name"] == class_name
        ]

        girls = len(
            class_df[
                class_df["gender"] == "Girl"
            ]
        )

        boys = len(
            class_df[
                class_df["gender"] == "Boy"
            ]
        )

        total = len(class_df)

        print(
            f"{class_name}: "
            f"Girls={girls}, "
            f"Boys={boys}, "
            f"Total={total}"
        )


def import_students():

    Base.metadata.create_all(
        bind=engine
    )

    if not EXCEL_FILE.exists():

        print(
            "\nERROR: Excel file not found"
        )

        print(EXCEL_FILE)

        return

    db = SessionLocal()

    try:

        print(
            "\n==================================="
        )

        print(
            "MULTI-SCHOOL STUDENT IMPORT"
        )

        print(
            "==================================="
        )

        (
            school_name,
            school_address,
            udise_code
        ) = read_school_details()

        print(
            "\nSCHOOL DETAILS FROM EXCEL"
        )

        print(
            f"Name    : {school_name}"
        )

        print(
            f"Address : {school_address}"
        )

        print(
            f"UDISE   : {udise_code}"
        )

        school = get_or_create_school(
            db,
            school_name,
            school_address,
            udise_code
        )

        df = read_students()

        print_classwise_extract(df)

        added = 0
        skipped = 0

        print(
            "\nIMPORTING STUDENTS..."
        )

        for _, row in df.iterrows():

            existing = (
                db.query(Student)
                .filter(
                    Student.school_id
                    == school.id,

                    Student.name
                    == row["name"],

                    Student.class_name
                    == row["class_name"],

                    Student.gender
                    == row["gender"]
                )
                .first()
            )

            if existing:

                skipped += 1

                continue

            student = Student(
                school_id=school.id,

                name=row["name"],

                class_name=row[
                    "class_name"
                ],

                gender=row["gender"],

                admission_no=None,

                pen_number=None,

                category=None,

                admission_date=None,

                is_active=True
            )

            db.add(student)

            added += 1

        db.commit()

        print(
            "\n==================================="
        )

        print(
            "IMPORT COMPLETED SUCCESSFULLY"
        )

        print(
            "==================================="
        )

        print(
            f"School ID       : {school.id}"
        )

        print(
            f"School Name     : "
            f"{school.school_name}"
        )

        print(
            f"School Address  : "
            f"{school.address}"
        )

        print(
            f"UDISE Code      : "
            f"{school.udise_code}"
        )

        print(
            f"Students Added  : {added}"
        )

        print(
            f"Students Skipped: {skipped}"
        )

        print(
            f"Total Processed : "
            f"{added + skipped}"
        )

    except Exception as e:

        db.rollback()

        print(
            "\n==================================="
        )

        print(
            "IMPORT FAILED"
        )

        print(
            "==================================="
        )

        print(e)

    finally:

        db.close()


if __name__ == "__main__":

    import_students()