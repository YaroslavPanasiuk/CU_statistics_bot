import json
import os
import os.path
import random
import string
import time
import datetime
from time import sleep

import pytz
import pandas as pd

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ParseMode, \
    ReplyKeyboardRemove, Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, ConversationHandler, \
    ContextTypes
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

(ENTER_SEARCHERS, ENTER_GOSPEL, ENTER_TEAMMATE, EXIT_CONVERSATION, ENTER_NAME, ASK_NAME) = range(6)
weekdays = {"monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6, "sunday": 0}

class Volunteer:
    def __init__(self, values: []):
        self.id = values[0]
        self.name = values[1]
        self.nickname = ''
        self.remind_statistics = True
        if len(values) == 3:
            self.remind_statistics = values[2]
        if len(values) > 3:
            self.searchers = values[2]
            self.gospel = values[3]
            self.teammate = values[4]


def read_config(value) -> str:
    file = open('config.txt', encoding='UTF-8')
    lines = file.readlines()
    file.close()
    for line in lines:
        if line.split(" = ")[0] == value:
            result_lines = line.split(" = ")[1].strip().split('\\n')
            result = ''
            for result_line in result_lines:
                result += result_line + '\n'
            return result[:len(result) - 1]
    return ''


def connect_to_spreadsheets():
    creds = None
    scopes = [read_config("SCOPES")]
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', scopes)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds


def get_spreadsheets_data():
    try:
        service = build('sheets', 'v4', credentials=connect_to_spreadsheets())
        sheet = service.spreadsheets()
        questions = sheet.values().get(spreadsheetId=read_config("SAMPLE_SPREADSHEET_ID_OLD"),
                                       range=read_config('QUESTIONS_RANGE_NAME')).execute().get('values', [])
        statistics = sheet.values().get(spreadsheetId=read_config("SAMPLE_SPREADSHEET_ID"),
                                        range=read_config('VOLUNTEERS_NAME_RANGE')).execute().get('values')
        volunteers_data = sheet.values().get(spreadsheetId=read_config("SAMPLE_SPREADSHEET_ID_OLD"),
                                        range=read_config('VOLUNTEERS_RANGE_NAME')).execute().get('values')
        if not questions:
            print('No questions found.')
            return
        if not statistics:
            statistics = [[]]
        if not volunteers_data:
            volunteers_data = [[]]
        volunteers = []
        for student in volunteers_data:
            volunteers.append(Volunteer([int(student[0]), student[1]]))
        questions_df = pd.DataFrame(questions)
        statistics_df = pd.DataFrame(statistics)
        statistics_df.columns = statistics_df.iloc[0]
        statistics_df = statistics_df[2:]
        return {'questions': questions_df, 'statistics': statistics_df, 'volunteers': volunteers}

    except HttpError as err:
        print(err)


def update_volunteers(students):
    try:
        service = build('sheets', 'v4', credentials=connect_to_spreadsheets())
        data_range = read_config("VOLUNTEERS_RANGE_NAME")
        data = []
        for student in students:
            data.append([student.id, student.name])
        for i in range(99-len(students)):
            data.append(['', ''])
        service.spreadsheets().values().update(
            spreadsheetId=read_config("SAMPLE_SPREADSHEET_ID_OLD"),
            valueInputOption='RAW',
            range=data_range,
            body=dict(
                majorDimension='ROWS',
                values=data
            )
        ).execute()

    except HttpError as err:
        print(err)

    with open('Volunteers.json', 'r+') as f:
        f.seek(0)  # <--- should reset file position to the beginning.
        data = []
        for student in students:
            data.append({'id': student.id, 'name': student.name, 'remind_statistics': student.remind_statistics})
        json.dump(data, f, indent=4)
        f.truncate()

def add_volunteer(volunteer: Volunteer):
    service = build('sheets', 'v4', credentials=connect_to_spreadsheets())
    students = get_volunteers()
    volunteer_exists = False
    for student in students:
        if student.id == volunteer.id:
            student.name = volunteer.name
            volunteer_exists = True
            break
    if not volunteer_exists:
        students.append(Volunteer([volunteer.id, volunteer.name]))
    update_volunteers(students)

    student_names = [student.name for student in students]
    while len(student_names) < int(read_config('MAX_VOLUNTEERS')):
        student_names.append('')

    data_range = 'БЛАГОВІСТЯ (Бот)!B3:B{0}'.format(len(student_names) + 2)
    service.spreadsheets().values().update(
        spreadsheetId=read_config("SAMPLE_SPREADSHEET_ID"),
        valueInputOption='RAW',
        range=data_range,
        body=dict(
            majorDimension='COLUMNS',
            values=[student_names]
        )
    ).execute()


def get_volunteer_index(volunteer: Volunteer):
    service = build('sheets', 'v4', credentials=connect_to_spreadsheets())
    student_names = service.spreadsheets().values().get(spreadsheetId=read_config("SAMPLE_SPREADSHEET_ID"),
                                                        range=read_config("VOLUNTEERS_NAME_RANGE")).execute().get('values')
    final_names = []
    for name in student_names:
        final_names.append(name[0])
    for i in range(len(final_names)):
        if final_names[i] == volunteer.name:
            return i + 1
    return None


def is_admin(user_id: int):
    admin_ids = read_config("ADMIN_IDS").split(", ")
    return str(user_id) in admin_ids


def get_volunteers():
    file = open('Volunteers.json', encoding='UTF-8')
    lines = json.load(file)
    result = []
    file.close()
    for line in lines:
        result.append(Volunteer([line.get("id"), line.get("name"), line.get("remind_statistics")]))
    return result


def get_volunteer_ids():
    volunteers = get_volunteers()
    result = []
    for volunteer in volunteers:
        result.append(volunteer.id)
    return result


def get_volunteer_names():
    volunteers = get_volunteers()
    result = []
    for volunteer in volunteers:
        result.append(volunteer.name)
    return result


def get_volunteer_name(volunteer_id):
    volunteers = get_volunteers()
    for volunteer in volunteers:
        if volunteer.id == int(volunteer_id):
            return volunteer.name
    return 'noName'

def get_current_columns():
    first_week = 36
    first_column = 3
    current_time = datetime.datetime.now(pytz.timezone('Europe/Kiev'))
    #current_time = datetime.date(2024, 9, 23)
    threshold_day = current_time + datetime.timedelta(days=-weekdays.get(read_config("THRESHOLD")))
    current_week = threshold_day.isocalendar().week
    columns = 3
    start_column = number_to_excel_column((current_week-first_week) * columns + first_column)
    end_column = number_to_excel_column((current_week-first_week + 1) * columns + first_column - 1)
    return start_column, end_column

def volunteer_filled_statistics(volunteer):
    try:
        service = build('sheets', 'v4', credentials=connect_to_spreadsheets())
        sheet = service.spreadsheets()
        start, end = get_current_columns()
        range_name = 'БЛАГОВІСТЯ (Бот)!{0}{2}:{1}{2}'.format(start, end, get_volunteer_index(volunteer) + 2)
        statistics = sheet.values().get(spreadsheetId=read_config("SAMPLE_SPREADSHEET_ID"),
                                        range=range_name).execute().get('values')
        if statistics is None:
            return False
        return True

    except HttpError as err:
        print(err)
        return False

def volunteer_exists(id):
    return id in get_volunteer_ids()

def number_to_excel_column(num: int):
    result = ""
    while num > 0:
        modulo = (num - 1) % 26
        result = chr(ord("A") + modulo) + result
        num = (num - modulo) // 26
    return result

def excel_column_to_number(col):
    num = 0
    for c in col:
        if c in string.ascii_letters:
            num = num * 26 + (ord(c.upper()) - ord('A')) + 1
    return num


def get_text(text):
    file = open('texts.json', encoding='UTF-8')
    content = json.load(file)
    file.close()
    if content.get(text) is not None:
        return content.get(text)
    return ''


def arrange_keyboard(count, columns):
    result = []
    for i in range(count // columns):
        row = []
        for j in range(columns):
            row.append(i * columns + j)
        result.append(row)
    row = []
    for i in range(count % columns):
        row.append(count - count % columns + i)
    result.append(row)
    return result


def update_texts():
    questions = get_spreadsheets_data().get("questions")
    file = open("texts.json", "w", encoding='UTF-8')
    data = questions.values
    dictionary = {}
    for row in data:
        dictionary[row[0]] = row[1]
    file.write(json.dumps(dictionary, indent=4, ensure_ascii=False))
    file.close()

def start_command(update, context):
    context.bot.send_message(chat_id=update.message.chat_id, text='hello')


def select_random_question(text):
    questions = text.split(';;')
    return questions[random.randint(0, len(questions) - 1)]

def check_statistics_availability(id):
    if not volunteer_exists(id):
        return False, "NOT_REGISTERED"
    start, end = get_current_columns()
    if excel_column_to_number(start) < excel_column_to_number("C"):
        return False, "SEMESTER_NOT_STARTED"
    if excel_column_to_number(end) > excel_column_to_number("AU"):
        return False, "SEMESTER_OVER"
    return True, ""

def question_1(update, context):
    id = update.message.chat_id
    available, message = check_statistics_availability(id)
    if not available:
        context.bot.send_message(chat_id=id, text=select_random_question(get_text(message)).format(
            get_volunteer_name(id).split(" ")[1]))
        return ConversationHandler.END
    context.bot.send_message(chat_id=id, text=select_random_question(get_text('QUESTION_1')),
                             reply_markup=ReplyKeyboardMarkup(arrange_keyboard(9, 3),
                                                              one_time_keyboard=True, resize_keyboard=True))
    return ENTER_GOSPEL


def question_2(update, context):
    if not update.message.text.isnumeric():
        context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('VALIDATION_FAILED_NUMERIC')),
                                 reply_markup=ReplyKeyboardMarkup(arrange_keyboard(9, 3),
                                                                  one_time_keyboard=True, resize_keyboard=True))
        return ENTER_GOSPEL
    context.user_data['searchers'] = update.message.text
    context.bot.send_message(chat_id=update.message.chat_id,
                             text=select_random_question(get_text('QUESTION_2')),
                             reply_markup=ReplyKeyboardMarkup(arrange_keyboard(9, 3),
                                                              one_time_keyboard=True, resize_keyboard=True))
    return ENTER_TEAMMATE


def question_3(update, context):
    if not update.message.text.isnumeric():
        context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('VALIDATION_FAILED_NUMERIC')),
                                 reply_markup=ReplyKeyboardMarkup(arrange_keyboard(9, 3),
                                                                  one_time_keyboard=True, resize_keyboard=True))
        return ENTER_TEAMMATE
    context.user_data['gospel'] = update.message.text
    context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('QUESTION_3')),
                             reply_markup=ReplyKeyboardMarkup(arrange_keyboard(9, 3),
                                                              one_time_keyboard=True, resize_keyboard=True))
    return EXIT_CONVERSATION


def exit_conversation(update, context):
    if not update.message.text.isnumeric():
        context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('VALIDATION_FAILED_NUMERIC')),
                                 reply_markup=ReplyKeyboardMarkup(arrange_keyboard(9, 3),
                                                                  one_time_keyboard=True, resize_keyboard=True))
        return EXIT_CONVERSATION
    context.user_data['teammate'] = update.message.text
    context.bot.send_message(chat_id=update.message.chat_id,
                             text=select_random_question(get_text('WRITING')),
                             reply_markup=ReplyKeyboardRemove())
    write_statistics_to_spreadsheets(
        Volunteer([str(update.message.chat_id), get_volunteer_name(str(update.message.chat_id)),
                   context.user_data['searchers'], context.user_data['gospel'],
                   context.user_data['teammate']]))
    context.bot.send_message(chat_id=update.message.chat_id,
                             text=select_random_question(get_text('STATISTICS_GATHERED')),
                             reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def ask_name(update, context):
    context.user_data.clear()
    context.user_data['id'] = str(update.message.chat_id)
    context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('GREETING')))
    context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('ASK_NAME')))
    return ENTER_NAME


def enter_name(update, context):
    chat_id = update.message.chat_id
    if len(update.message.text.split(" ")) != 2:
        context.bot.send_message(chat_id=chat_id, text=select_random_question(get_text('VALIDATION_FAILED_NAME')).format(update.message.text))
        return ENTER_NAME
    context.user_data['name'] = update.message.text
    context.bot.send_message(chat_id=chat_id, text=select_random_question(get_text('WRITING')))
    add_volunteer(Volunteer([int(context.user_data['id']), context.user_data['name']]))
    context.bot.send_message(chat_id=chat_id,
                             text=select_random_question(get_text('REGISTRATION_COMPLETE')).format(
                                 get_volunteer_name(chat_id).split(" ")[-1]))
    restart_jobs(context.job_queue)
    return ConversationHandler.END


def write_statistics_to_spreadsheets(volunteer: Volunteer):
    service = build('sheets', 'v4', credentials=connect_to_spreadsheets())
    start, end = get_current_columns()
    row = get_volunteer_index(volunteer) + 2
    data_range = 'БЛАГОВІСТЯ (Бот)!{0}{2}:{1}{2}'.format(start, end, row)

    data = [volunteer.searchers, volunteer.gospel, volunteer.teammate]

    service.spreadsheets().values().update(
        spreadsheetId=read_config("SAMPLE_SPREADSHEET_ID"),
        valueInputOption='RAW',
        range=data_range,
        body=dict(
            majorDimension='ROWS',
            values=[data]
        )
    ).execute()
    return


def end_conversation(update, context):
    return ConversationHandler.END


def reminder(context):
    id = int(context.job.context[0])
    if len(context.job.context) > 1:
        order = context.job.context[1]
    else:
        order = 1
    if volunteer_filled_statistics(Volunteer([id, get_volunteer_name(id)])):
        context.bot.send_message(chat_id=int(read_config("ADMIN_ID")),
                                 text="{0} already filled statistics".format(get_volunteer_name(id)))
        return
    message = 'REMINDER'
    if order == 2:
        message = 'REMINDER2'
    if order == 3:
        message = 'REMINDER3'
    if order == 4:
        message = 'REMINDER_LATE'
    if order == 5:
        message = 'REMINDER_SORRY'
    try:
        keyboard = [[KeyboardButton(select_random_question(get_text('FILL_STATISTICS')))]]
        context.bot.send_message(chat_id=id,
                                 text=select_random_question(get_text(message)).format(get_volunteer_name(id).split(" ")[1]),
                                 reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True,
                                                                  resize_keyboard=True), parse_mode=ParseMode.HTML)
        sleep(1)
        context.bot.send_message(chat_id=int(read_config("ADMIN_ID")),
                                 text="reminded {0}".format(get_volunteer_name(id)))
    except:
        context.bot.send_message(chat_id=int(read_config('ADMIN_ID')),
                                 text='chat {0} of {1} not found'.format(id, get_volunteer_name(id)))


def spam_volunteers(update, context):
    if not is_admin(update.message.chat_id):
        return
    order = 1
    if len(context.args) == 1:
        order = int(context.args[0])
    for id in get_volunteer_ids():
        context.job_queue.run_once(reminder, 0, context=[id, order])
        time.sleep(2)

def spam_admin(update, context):
    id = update.message.chat_id
    print(id)
    order = 3
    if len(context.args) == 1:
        order = int(context.args[0])
    if not is_admin(id):
        print("not admin")
        return
    time.sleep(2)
    context.job_queue.run_once(reminder, 0, context=[id, order])
    print("sent to {0}".format(id))

def restart_jobs(job_queue):
    delay_seconds = 0
    for job in job_queue.jobs():
        job.schedule_removal()
    for id in get_volunteer_ids():
        job_queue.run_daily(reminder,
                            time=datetime.time(hour=17, minute=(delay_seconds // 60) % 60, second=delay_seconds % 60, tzinfo=pytz.timezone('Europe/Kyiv')),
                            days=[6], context=[id, 1], name=str(id))
        delay_seconds += 2
        job_queue.run_daily(reminder,
                            time=datetime.time(hour=19, minute=(delay_seconds // 60) % 60, second=delay_seconds % 60, tzinfo=pytz.timezone('Europe/Kyiv')),
                            days=[6], context=[id, 2], name=str(id))
        delay_seconds += 2
        job_queue.run_daily(reminder,
                            time=datetime.time(hour=21, minute=(delay_seconds // 60) % 60, second=delay_seconds % 60, tzinfo=pytz.timezone('Europe/Kyiv')),
                            days=[6], context=[id, 3], name=str(id))
        delay_seconds += 2

def show_menu(update, context):
    context.bot.send_message(chat_id=update.message.chat_id, text=get_text('MENU'), parse_mode=ParseMode.HTML)


def running_jobs(update, context):
    if len(context.job_queue.jobs()) == 0:
        context.bot.send_message(chat_id=update.message.chat_id, text='no running jobs')
        return
    reply = ''
    for job in context.job_queue.jobs():
        reply += '{0}, {1}, {2}\n'.format(job.context, get_volunteer_name(job.context[0]), job.next_t)
        if len(reply) > 3500:
            context.bot.send_message(chat_id=update.message.chat_id, text=reply)
            reply = ''
    context.bot.send_message(chat_id=update.message.chat_id, text=reply)


def main():
    update_texts()
    update_volunteers(get_spreadsheets_data().get("volunteers"))
    print("updated")
    updater = Updater(read_config("BOT_TOKEN"), use_context=True)
    dispatcher = updater.dispatcher
    restart_jobs(updater.job_queue)
    # dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))
    register_conversation_handler = ConversationHandler(
        entry_points=[CommandHandler('start', ask_name)],
        states={ENTER_NAME: [MessageHandler(Filters.text & (~ Filters.command), enter_name)],
                ASK_NAME: [MessageHandler(Filters.text & (~ Filters.command), ask_name)]},
        fallbacks=[]
    )
    gather_statistics_conversation_handler = ConversationHandler(
        entry_points=[CommandHandler('send_statistics', question_1),
                      MessageHandler(Filters.text(select_random_question(get_text('FILL_STATISTICS'))), question_1)],
        states={
            ENTER_GOSPEL: [MessageHandler(Filters.text & (~ Filters.command), question_2)],
            ENTER_TEAMMATE: [MessageHandler(Filters.text & (~ Filters.command), question_3)],
            EXIT_CONVERSATION: [MessageHandler(Filters.text & (~ Filters.command), exit_conversation)]
        },
        fallbacks=[CommandHandler('finish', end_conversation)]
    )
    dispatcher.add_handler(register_conversation_handler)
    dispatcher.add_handler(gather_statistics_conversation_handler)
    dispatcher.add_handler(CommandHandler("spam_admin", spam_admin))
    dispatcher.add_handler(CommandHandler("spam_volunteers", spam_volunteers))
    dispatcher.add_handler(CommandHandler("running_jobs", running_jobs))
    dispatcher.add_handler(CommandHandler("menu", show_menu))
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()