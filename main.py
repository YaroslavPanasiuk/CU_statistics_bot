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

(ENTER_SEARCHERS, ENTER_GOSPEL, ENTER_TEAMMATE, EXIT_CONVERSATION, ENTER_NAME) = range(5)

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
        if not questions:
            print('No questions found.')
            return
        if not statistics:
            statistics = [[]]
        questions_df = pd.DataFrame(questions)
        statistics_df = pd.DataFrame(statistics)
        statistics_df.columns = statistics_df.iloc[0]
        statistics_df = statistics_df[2:]
        return {'questions': questions_df, 'statistics': statistics_df}

    except HttpError as err:
        print(err)


def update_volunteers(students):
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
    current_time = datetime.datetime.now(pytz.timezone('Europe/Kiev'))
    #current_time = datetime.date(2024, 9, 16)
    current_week = current_time.isocalendar().week
    columns = 2
    start_column = number_to_excel_column(current_week * 2 - 69)
    end_column = number_to_excel_column(current_week * 2 + columns - 70)
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

def set_volunteer_spam(volunteer_id, reminder):
    data = json.load(open("Volunteers.json"))
    for volunteer in data:
        if volunteer.get("id") == volunteer_id:
            volunteer["remind_statistics"] = reminder
            with open('Volunteers.json', 'r+') as f:
                f.seek(0)
                json.dump(data, f, indent=4)
                f.truncate()
            return True
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

def ask_searchers(update, context):
    if not volunteer_exists(update.message.chat_id):
        context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('NOT_REGISTERED')))
        return ConversationHandler.END
    context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('ASK_SEARCHERS')),
                             reply_markup=ReplyKeyboardMarkup(arrange_keyboard(9, 3),
                                                              one_time_keyboard=True, resize_keyboard=True))
    return ENTER_GOSPEL


def ask_gospel(update, context):
    context.user_data['searchers'] = update.message.text
    context.bot.send_message(chat_id=update.message.chat_id,
                             text=select_random_question(get_text('ASK_GOSPEL')),
                             reply_markup=ReplyKeyboardMarkup(arrange_keyboard(9, 3),
                                                              one_time_keyboard=True, resize_keyboard=True))
    return ENTER_TEAMMATE


def ask_teammate(update, context):
    keyboard = [[KeyboardButton(get_text('YES')), KeyboardButton(get_text('NO'))]]
    context.user_data['gospel'] = update.message.text
    context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('ASK_TEAMMATE')),
                             reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True))
    return EXIT_CONVERSATION


def exit_conversation(update, context):
    context.user_data['teammate'] = update.message.text
    context.bot.send_message(chat_id=update.message.chat_id,
                             text=select_random_question(get_text('WRITING')),
                             reply_markup=ReplyKeyboardRemove())
    message = write_statistics_to_spreadsheets(
        Volunteer([str(update.message.chat_id), get_volunteer_name(str(update.message.chat_id)),
                   context.user_data['searchers'], context.user_data['gospel'],
                   context.user_data['teammate']]))
    context.bot.send_message(chat_id=update.message.chat_id,
                             text=select_random_question(get_text(message)),
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
    context.user_data['name'] = update.message.text
    context.bot.send_message(chat_id=chat_id,
                             text=select_random_question(get_text('REGISTRATION_COMPLETE')).format(get_volunteer_name(id).split(" ")[1]))
    add_volunteer(Volunteer([int(context.user_data['id']), context.user_data['name']]))
    restart_jobs(context.job_queue)
    return ConversationHandler.END


def write_statistics_to_spreadsheets(volunteer: Volunteer):
    service = build('sheets', 'v4', credentials=connect_to_spreadsheets())
    start, end = get_current_columns()
    row = get_volunteer_index(volunteer) + 2
    if excel_column_to_number(start) < excel_column_to_number("C") :
        return 'SEMESTER_NOT_STARTED'
    if excel_column_to_number(end) > excel_column_to_number("AF") :
        return 'SEMESTER_OVER'

    data_range = 'БЛАГОВІСТЯ (Бот)!{0}{2}:{1}{2}'.format(start, end, row)

    data = [volunteer.gospel, volunteer.teammate]

    service.spreadsheets().values().update(
        spreadsheetId=read_config("SAMPLE_SPREADSHEET_ID"),
        valueInputOption='RAW',
        range=data_range,
        body=dict(
            majorDimension='ROWS',
            values=[data]
        )
    ).execute()

    return 'STATISTICS_GATHERED'


def end_conversation(update, context):
    return ConversationHandler.END


def reminder(context):
    id = int(context.job.context)
    if volunteer_filled_statistics(Volunteer([id, get_volunteer_name(id)])):
        return
    try:
        keyboard = [[KeyboardButton(select_random_question(get_text('FILL_STATISTICS')))]]
        context.bot.send_message(chat_id=id,
                                 text=select_random_question(get_text('REMINDER')).format(get_volunteer_name(id).split(" ")[1]),
                                 reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True,
                                                                  resize_keyboard=True))
        sleep(1)
        context.bot.send_message(chat_id=int(read_config("ADMIN_ID")),
                                 text="reminded {0}".format(get_volunteer_name(id)))
    except:
        context.bot.send_message(chat_id=int(read_config('ADMIN_ID')),
                                 text='chat {0} of {1} not found'.format(id, get_volunteer_name(id)))


def spam_volunteers(update, context):
    if not is_admin(update.message.chat_id):
        return
    for id in get_volunteer_ids():
        context.job_queue.run_once(reminder, 0, context=id)
        time.sleep(2)

def spam_admin(update, context):
    if not is_admin(update.message.chat_id):
        print("not admin")
        return
    time.sleep(4)
    context.job_queue.run_once(reminder, 0, context=update.message.chat_id)
    print("sent")

def restart_jobs(job_queue):
    delay_seconds = 0
    for job in job_queue.jobs():
        job.schedule_removal()
    for id in get_volunteer_ids():
        job_queue.run_daily(reminder,
                                    time=datetime.time(hour=17, minute=(delay_seconds // 60) % 60, second=delay_seconds % 60),
                                    days=[6],
                                    context=id, name=str(id))
        delay_seconds += 2
        job_queue.run_daily(reminder,
                                    time=datetime.time(hour=19, minute=(delay_seconds // 60) % 60, second=delay_seconds % 60),
                                    days=[6],
                                    context=id, name=str(id))
        delay_seconds += 2
        job_queue.run_daily(reminder,
                                    time=datetime.time(hour=21, minute=(delay_seconds // 60) % 60, second=delay_seconds % 60),
                                    days=[6],
                                    context=id, name=str(id))
        delay_seconds += 2
    print(delay_seconds)

def show_menu(update, context):
    context.bot.send_message(chat_id=update.message.chat_id, text=get_text('MENU'))


def running_jobs(update, context):
    if len(context.job_queue.jobs()) == 0:
        context.bot.send_message(chat_id=update.message.chat_id, text='no running jobs')
        return
    reply = ''
    for job in context.job_queue.jobs():
        reply += '{0}, {1}, {2}\n'.format(job.name, get_volunteer_name(job.name), job.next_t)
    context.bot.send_message(chat_id=update.message.chat_id, text=reply)


def main():
    update_texts()
    updater = Updater(read_config("TEST_BOT_TOKEN"), use_context=True)
    dispatcher = updater.dispatcher
    restart_jobs(updater.job_queue)
    # dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))
    register_conversation_handler = ConversationHandler(
        entry_points=[CommandHandler('start', ask_name)],
        states={ENTER_NAME: [MessageHandler(Filters.text & (~ Filters.command), enter_name)]},
        fallbacks=[]
    )
    gather_statistics_conversation_handler = ConversationHandler(
        entry_points=[CommandHandler('send_statistics', ask_searchers),
                      MessageHandler(Filters.text(get_text('FILL_STATISTICS').split(';;')), ask_searchers)],
        states={
            ENTER_GOSPEL: [MessageHandler(Filters.text & (~ Filters.command), ask_gospel)],
            ENTER_TEAMMATE: [MessageHandler(Filters.text & (~ Filters.command), ask_teammate)],
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
