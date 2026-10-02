# -*- coding: utf-8 -*-
"""Two made-up months of attendance, in the exact shape of a Hilan page.

The markup is Hilan's — element ids, CSS classes, the way an empty cell is
written. Every value in it is invented: the employee, the contacts in the
summary panel, every clock time, every day type, absence and comment.

The months are built to hold the edge cases a real page throws up:

September (seen on 22/09, so 22/09 is "today"):
  01  a plain day, clock and report agreeing
  04  a Friday of work from home with no stated requirement
  07  two reported sessions, the second from home with no punch
  08  a turnstile entry with no exit, closed by a hand-filled row — the day
      Hilan's own summary leaves out, so the comparison has exactly its hours
      to find
  14  a day of vacation
  17  a Thursday with no punch, reported by hand with a comment
  20  Yom Kippur eve, a short worked day
  22  an open entry, and Hilan's "row without a project" message in the
      holiday cell
  23-30  rows that report nothing yet but state the day's requirement
August (a past month):
  04, 06      vacation, a full day and a Thursday
  05          work from home with no punch
  10, 11, 12  sick leave
  13          two reported sessions on a Thursday
  18          a turnstile entry with no exit, covered by a closed row
  25          only the clock-outs registered, across two sessions

``conftest.build_html`` renders it into the markup the page uses (cells carrying
an ``ov`` attribute, one ``<tr id="..._row_N">`` per day, segments suffixed
``_row_N_K``). Keeping it as data rather than a saved page keeps the repository
small and makes it obvious what each test depends on.
"""

BLANK = "&nbsp;"  # how Hilan writes an empty cell into the ov attribute

LEGEND = (
    "<p>יתרת חופשתך נכון לעיבוד השכר האחרון הינה: -12.50<br>"
    "תקן: 173.00 | תקן עד היום: 119.50<br>"
    "שעות בפועל: 97.25 | יצרניות: 96.94<br><br>"
    "הסכם: כללי, אגף: פיתוח<br>"
    "מנהל אגף: ישראל כהן 0500000000 manager@example.com <br>"
    "רפרנטית נוכחות: שרה לוי 00 attendance@example.com</p>"
)
SYNC = "נתוני שעון מעודכנים לתאריך 22/09/2026 16:40"
WHO = "ישראל ישראלי | 12345"

SEPTEMBER = {
    "gid": "ctl00_mp_RG_Days_100012345_2026_09",
    "who": WHO, "currentMonth": "01/09/2026", "sync": SYNC, "legend": LEGEND,
    "calendar": [
        (9740, 'cDIES CSD', '9:10'), (9741, 'cDIES CSD', '8:30'),
        (9742, 'cDIES CSD', '10:45'), (9743, 'cDIES CSD', '0:50'),
        (9744, 'cHD CSD', ''), (9745, 'cDIES CSD', '7:50'),
        (9746, 'cDIES CSD', '9:50'), (9747, 'cDIES CSD', '9:00'),
        (9748, 'cDIES CSD', '10:30'), (9749, 'cDIES CSD', '8:30'),
        (9750, 'cHD CSD', 'ערב חג'), (9751, 'cHD CSD', ''),
        (9752, 'cHD CSD', 'חג'), (9753, 'calendarAbcenseDay CSD', 'חופשה'),
        (9754, 'cDIES CSD', '9:10'), (9755, 'cDIES CSD', '9:30'),
        (9756, 'cDIES CSD', '8:40'), (9757, 'cDIES CSD', ''),
        (9758, 'cHD CSD', ''), (9759, 'cHD CSD', '4:00'),
        (9760, 'cHD CSD', 'חג'), (9761, 'cDIES CSD currentDay cED', 'נכח'),
        (9762, 'cDIES CSD', ''), (9763, 'cDIES CSD', ''),
        (9764, 'cHD CSD', 'ערב חג'), (9765, 'cHD CSD', ''),
        (9766, 'cHD CSD', 'חול המועד'), (9767, 'cHD CSD', 'חול המועד'),
        (9768, 'cHD CSD', 'חול המועד'), (9769, 'cHD CSD', 'חול המועד'),
    ],
    "rows": [
        (0, '01/09', 'ג', None, [('07:55', '17:05')],
         [('07:55', '17:05', '09:10', '9.00', '', 'נוכחות')]),
        (1, '02/09', 'ד', None, [('08:20', '16:50')],
         [('08:20', '16:50', '08:30', '9.00', '', 'נוכחות')]),
        (2, '03/09', 'ה', None, [('07:45', '18:30')],
         [('07:45', '18:30', '10:45', '8.50', '', 'נוכחות')]),
        (3, '04/09', 'ו', None, [(BLANK, BLANK)],
         [('09:30', '10:20', '00:50', '', '', 'עבודה מהבית')]),
        (4, '05/09', 'שבת', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (5, '06/09', 'א', None, [('08:30', '16:20')],
         [('08:30', '16:20', '07:50', '9.00', '', 'נוכחות')]),
        (6, '07/09', 'ב', None, [('08:00', '17:05'), (BLANK, BLANK)],
         [('08:00', '17:05', '09:05', '9.00', '', 'נוכחות'), ('20:00', '20:45', '00:45', '9.00', '', 'עבודה מהבית')]),
        (7, '08/09', 'ג', None, [('08:40', BLANK)],
         [('08:40', '17:40', '09:00', '9.00', 'פגישה מחוץ למשרד', 'נוכחות')]),
        (8, '09/09', 'ד', None, [('07:50', '18:20')],
         [('07:50', '18:20', '10:30', '9.00', '', 'נוכחות')]),
        (9, '10/09', 'ה', None, [(BLANK, BLANK)],
         [('08:10', '16:40', '08:30', '8.50', '', 'נוכחות')]),
        (10, '11/09', 'ו', 'ערב חג', [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (11, '12/09', 'שבת', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (12, '13/09', 'א', 'חג', [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (13, '14/09', 'ב', None, [(BLANK, BLANK)],
         [('', '', BLANK, '9.00', '', 'חופשה')]),
        (14, '15/09', 'ג', None, [('08:20', '17:30')],
         [('08:20', '17:30', '09:10', '9.00', '', 'נוכחות')]),
        (15, '16/09', 'ד', None, [('08:15', '17:45')],
         [('08:15', '17:45', '09:30', '9.00', '', 'נוכחות')]),
        (16, '17/09', 'ה', None, [(BLANK, BLANK)],
         [('08:05', '16:45', '08:40', '8.50', 'דיווח ידני', 'נוכחות')]),
        (17, '18/09', 'ו', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (18, '19/09', 'שבת', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (19, '20/09', 'א', 'ערב יוה"כ', [('08:00', '12:00')],
         [('08:00', '12:00', '04:00', '4.00', '', 'נוכחות')]),
        (20, '21/09', 'ב', 'חג', [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (21, '22/09', 'ג', 'קיים דיווח ללא פרויקט באותה השורה', [('08:15', BLANK)],
         [('08:15', '', BLANK, '9.00', '', 'נוכחות')]),
        (22, '23/09', 'ד', None, [(BLANK, BLANK)],
         [('', '', BLANK, '9.00', '', '')]),
        (23, '24/09', 'ה', None, [(BLANK, BLANK)],
         [('', '', BLANK, '8.50', '', '')]),
        (24, '25/09', 'ו', 'ערב חג', [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (25, '26/09', 'שבת', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (26, '27/09', 'א', 'חול המועד', [(BLANK, BLANK)],
         [('', '', BLANK, '9.00', '', '')]),
        (27, '28/09', 'ב', 'חול המועד', [(BLANK, BLANK)],
         [('', '', BLANK, '9.00', '', '')]),
        (28, '29/09', 'ג', 'חול המועד', [(BLANK, BLANK)],
         [('', '', BLANK, '9.00', '', '')]),
        (29, '30/09', 'ד', 'חול המועד', [(BLANK, BLANK)],
         [('', '', BLANK, '9.00', '', '')]),
    ],
}

AUGUST = {
    "gid": "ctl00_mp_RG_Days_100012345_2026_08",
    "who": WHO, "currentMonth": "01/08/2026", "sync": SYNC,
    # The legend panel always reflects the current payroll month, never the one
    # being browsed — so August's page still shows September's totals.
    "legend": LEGEND,
    "calendar": [
        (9709, 'cHD CSD', ''), (9710, 'cDIES CSD', '8:45'),
        (9711, 'cDIES CSD', '9:20'), (9712, 'calendarAbcenseDay CSD', 'חופשה'),
        (9713, 'cDIES CSD', '8:45'), (9714, 'calendarAbcenseDay CSD', 'חופשה'),
        (9715, 'cDIES CSD', ''), (9716, 'cHD CSD', ''),
        (9717, 'cDIES CSD', '7:15'), (9718, 'calendarAbcenseDay CSD', 'מחלה'),
        (9719, 'calendarAbcenseDay CSD', 'מחלה'), (9720, 'calendarAbcenseDay CSD', 'מחלה'),
        (9721, 'cDIES CSD', '9:30'), (9722, 'cDIES CSD', ''),
        (9723, 'cHD CSD', ''), (9724, 'cDIES CSD', '10:50'),
        (9725, 'cDIES CSD', '10:40'), (9726, 'cDIES CSD', '7:30'),
        (9727, 'cDIES CSD', '9:30'), (9728, 'cDIES CSD', '9:30'),
        (9729, 'cDIES CSD', ''), (9730, 'cHD CSD', ''),
        (9731, 'cDIES CSD', '8:15'), (9732, 'cDIES CSD', '8:10'),
        (9733, 'cDIES CSD', '9:10'), (9734, 'cDIES CSD', '9:50'),
        (9735, 'cDIES CSD', '8:45'), (9736, 'cDIES CSD', ''),
        (9737, 'cHD CSD', ''), (9738, 'cDIES CSD', '11:30'),
        (9739, 'cDIES CSD', '7:30'),
    ],
    "rows": [
        (0, '01/08', 'שבת', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (1, '02/08', 'א', None, [('08:10', '16:55')],
         [('08:10', '16:55', '08:45', '9.00', '', 'נוכחות')]),
        (2, '03/08', 'ב', None, [('08:00', '17:20')],
         [('08:00', '17:20', '09:20', '9.00', '', 'נוכחות')]),
        (3, '04/08', 'ג', None, [(BLANK, BLANK)],
         [('', '', BLANK, '9.00', '', 'חופשה')]),
        (4, '05/08', 'ד', None, [(BLANK, BLANK)],
         [('09:00', '17:45', '08:45', '9.00', '', 'עבודה מהבית')]),
        (5, '06/08', 'ה', None, [(BLANK, BLANK)],
         [('', '', BLANK, '8.50', '', 'חופשה')]),
        (6, '07/08', 'ו', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (7, '08/08', 'שבת', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (8, '09/08', 'א', None, [('08:30', '15:45')],
         [('08:30', '15:45', '07:15', '9.00', '', 'נוכחות')]),
        (9, '10/08', 'ב', None, [(BLANK, BLANK)],
         [('', '', BLANK, '9.00', '', 'מחלה')]),
        (10, '11/08', 'ג', None, [(BLANK, BLANK)],
         [('', '', BLANK, '9.00', '', 'מחלה')]),
        (11, '12/08', 'ד', None, [(BLANK, BLANK)],
         [('', '', BLANK, '9.00', '', 'מחלה')]),
        (12, '13/08', 'ה', None, [('08:10', '15:40'), (BLANK, BLANK)],
         [('08:10', '15:40', '07:30', '8.50', '', 'נוכחות'), ('19:00', '21:00', '02:00', '8.50', '', 'נוכחות')]),
        (13, '14/08', 'ו', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (14, '15/08', 'שבת', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (15, '16/08', 'א', None, [('07:50', '18:40')],
         [('07:50', '18:40', '10:50', '9.00', '', 'נוכחות')]),
        (16, '17/08', 'ב', None, [('08:15', '18:55')],
         [('08:15', '18:55', '10:40', '9.00', '', 'נוכחות')]),
        (17, '18/08', 'ג', None, [('08:35', BLANK)],
         [('08:35', '16:05', '07:30', '9.00', '', 'נוכחות')]),
        (18, '19/08', 'ד', None, [('08:00', '17:30')],
         [('08:00', '17:30', '09:30', '9.00', '', 'נוכחות')]),
        (19, '20/08', 'ה', None, [('07:45', '17:15')],
         [('07:45', '17:15', '09:30', '8.50', '', 'נוכחות')]),
        (20, '21/08', 'ו', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (21, '22/08', 'שבת', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (22, '23/08', 'א', None, [('08:25', '16:40')],
         [('08:25', '16:40', '08:15', '9.00', '', 'נוכחות')]),
        (23, '24/08', 'ב', None, [('08:20', '16:30')],
         [('08:20', '16:30', '08:10', '9.00', '', 'נוכחות')]),
        (24, '25/08', 'ג', None, [(BLANK, '12:30'), (BLANK, '17:10')],
         [('08:00', '12:30', '04:30', '9.00', '', 'נוכחות'), ('12:30', '17:10', '04:40', '9.00', '', 'נוכחות')]),
        (25, '26/08', 'ד', None, [('08:10', '18:00')],
         [('08:10', '18:00', '09:50', '9.00', '', 'נוכחות')]),
        (26, '27/08', 'ה', None, [('08:05', '16:50')],
         [('08:05', '16:50', '08:45', '8.50', '', 'נוכחות')]),
        (27, '28/08', 'ו', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (28, '29/08', 'שבת', None, [(BLANK, BLANK)],
         [('', '', BLANK, '', '', '')]),
        (29, '30/08', 'א', None, [('08:00', '19:30')],
         [('08:00', '19:30', '11:30', '9.00', '', 'נוכחות')]),
        (30, '31/08', 'ב', None, [('08:30', '16:00')],
         [('08:30', '16:00', '07:30', '9.00', '', 'נוכחות')]),
    ],
}

CAPTURES = {"2026-09": SEPTEMBER, "2026-08": AUGUST}
