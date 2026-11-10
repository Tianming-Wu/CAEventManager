-- 这个模块在活动库里的表。平台先建自己的表（config / audit），再来执行这一份。
CREATE TABLE IF NOT EXISTS students (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sid         TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    py          TEXT,
    pyi         TEXT,
    grp         TEXT,
    note        TEXT,
    role        TEXT    NOT NULL DEFAULT 'participant',  -- participant | staff | volunteer
    origin      TEXT    NOT NULL DEFAULT 'manual',       -- manual | import | roster | signup
    detached_at REAL    NOT NULL DEFAULT 0,              -- 绑定的名单里已经没有这个人
    created_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS marks (
    student_id  INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    done_at     REAL    NOT NULL,
    source      TEXT,
    actor       TEXT,
    PRIMARY KEY (student_id, kind)
);

CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    code        INTEGER NOT NULL,
    assigned_at REAL    NOT NULL,
    released_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS tickets_live_code
    ON tickets(code) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS tickets_live_student
    ON tickets(student_id) WHERE released_at IS NULL;

CREATE TABLE IF NOT EXISTS draws (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seq         INTEGER NOT NULL,
    prize       TEXT,
    mode        TEXT    NOT NULL DEFAULT 'ticket',  -- 'ticket' | 'person'
    code        INTEGER,
    student_id  INTEGER,
    ts          REAL    NOT NULL,
    voided_at   REAL,
    batch       TEXT                                -- draws made by one click share it
);
