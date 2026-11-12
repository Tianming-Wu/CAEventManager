-- 讲座：议程与提问。平台建好自己的表之后执行这一段，可重复执行。
CREATE TABLE IF NOT EXISTS talk_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pos INTEGER NOT NULL DEFAULT 0,          -- 排在议程的第几位
    title TEXT NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 15,     -- 计划时长
    started_at REAL,                         -- 开始讲的时间，空表示还没开始
    ended_at REAL                            -- 讲完的时间，空表示还没结束
);

CREATE TABLE IF NOT EXISTS talk_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    who TEXT NOT NULL DEFAULT '',            -- 提问的人，留空就是匿名
    text TEXT NOT NULL,
    asked_at REAL NOT NULL,
    answered_at REAL                         -- 回答过了就有时间
);
