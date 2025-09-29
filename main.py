import json
import os
import os.path
import random
import string
import time
import datetime
from time import sleep
import socket
from urllib.parse import quote
import requests

import pytz
import pandas as pd
from math import floor
import re

from telegram.constants import ParseMode
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, \
    ReplyKeyboardRemove, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ConversationHandler, \
    ContextTypes
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

(ENTER_SEARCHERS, ENTER_GOSPEL, ENTER_TEAMMATE, EXIT_CONVERSATION, ENTER_NAME, ASK_NAME, EXIT_CONVERSATION_EARLY) = range(7)
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
            #self.teammate = values[4]


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
        try:
            creds = Credentials.from_authorized_user_file('token.json', scopes)
        except Exception as e:
            print(f"Error loading credentials: {e}")
            creds = None
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Error refreshing credentials: {e}")
                creds = None
        else:
            try:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', scopes)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                print(f"Error during OAuth flow: {e}")
                raise
    
    return creds


def get_sheets_values(spreadsheet_id, range_name):
    """Get values from Google Sheets using requests directly."""
    creds = connect_to_spreadsheets()
    if not creds or not creds.valid:
        print("No valid credentials available")
        return None
        
    access_token = creds.token
    # URL encode the range name to handle special characters
    encoded_range = quote(range_name)
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded_range}"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        # Force IPv4 to avoid IPv6 issues
        original_getaddrinfo = socket.getaddrinfo
        socket.getaddrinfo = lambda *args, **kwargs: original_getaddrinfo(*args, **kwargs)[:1]  # Force IPv4
        response = requests.get(url, headers=headers, timeout=30)
        socket.getaddrinfo = original_getaddrinfo  # Restore original function
    except Exception as e:
        print(f"Request to Sheets API failed: {e}")
        return None
        
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Sheets API error: {response.status_code} - {response.text}")
        return None


def get_spreadsheets_data():
    try:        
        # Get questions
        questions_result = get_sheets_values(read_config("SAMPLE_SPREADSHEET_ID_OLD"), read_config("QUESTIONS_RANGE_NAME"))
        questions = questions_result.get('values', [])
        
        # Get statistics
        statistics_result = get_sheets_values(read_config("SAMPLE_SPREADSHEET_ID"), read_config("VOLUNTEERS_NAME_RANGE"))
        statistics = statistics_result.get('values', [])
        
        # Get volunteers data
        volunteeers_result = get_sheets_values(read_config("SAMPLE_SPREADSHEET_ID_OLD"), read_config("VOLUNTEERS_RANGE_NAME"))
        volunteers_data = volunteeers_result.get('values', [])
        
        if not questions:
            print('No questions found.')
            return None
            
        if not statistics:
            statistics = [[]]
        if not volunteers_data:
            volunteers_data = [[]]
            
        volunteers = []
        for student in volunteers_data:
            if student:  # Check if student list is not empty
                volunteers.append(Volunteer([int(student[0]), student[1]]))
                
        questions_df = pd.DataFrame(questions)
        statistics_df = pd.DataFrame(statistics)
        if not statistics_df.empty:
            statistics_df.columns = statistics_df.iloc[0]
            statistics_df = statistics_df[2:]
            
        return {'questions': questions_df, 'statistics': statistics_df, 'volunteers': volunteers}

    except HttpError as err:
        print(f"HTTP Error: {err}")
        return None
    except Exception as e:
        print(f"Error getting spreadsheet data: {e}")
        return None


def get_statistics_from_spreadsheets(volunteer: Volunteer, weeks: list[int]):
    try:
        start = get_columns(weeks[0])[0]
        end = get_columns(weeks[-1])[-1]
        row = get_volunteer_index(volunteer)
        if row is None:
            print(f"Volunteer {volunteer.name} not found in spreadsheet")
            return
            
        data_range = f'Благовістя 2025!{start}{row + 2}:{end}{row + 2}'
        # Get questions
        statistics = get_sheets_values(read_config("SAMPLE_SPREADSHEET_ID"), data_range).get('values', [[]])[0]
        while len(statistics) < int(floor(len(weeks)*1.5)):
            statistics.append("")
        return statistics
    
    except HttpError as err:
        print(f"HTTP Error: {err}")
        return None
    except Exception as e:
        print(f"Error getting spreadsheet data: {e}")
        return None


def update_volunteers(students):
    try:
        creds = connect_to_spreadsheets()
        if not creds:
            print("No credentials for adding student")
            return            
        access_token = creds.token
        spreadsheet_id = read_config("SAMPLE_SPREADSHEET_ID_OLD")

        data = []
        for student in students:
            data.append([student.id, student.name])
        for i in range(99 - len(students)):
            data.append(['', ''])
            
        range_name = f"Аркуш2!A2:B100"
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{range_name}?valueInputOption=USER_ENTERED"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        body = {
            "values": data
        }

        original_getaddrinfo = socket.getaddrinfo
        socket.getaddrinfo = lambda *args, **kwargs: original_getaddrinfo(*args, **kwargs)[:1]
        response = requests.put(url, headers=headers, json=body, timeout=30)
        socket.getaddrinfo = original_getaddrinfo

        if response.status_code == 200:
            print("Volunteer names updated successfully in spreadsheet")
        else:
            print(f"Failed to update volunteer names: {response.status_code} - {response.text}")

    except HttpError as err:
        print(f"HTTP Error updating volunteers: {err}")
    except Exception as e:
        print(f"Error updating volunteers: {e}")

    with open('data/Volunteers.json', 'r+') as f:
        f.seek(0)
        data = []
        for student in students:
            data.append({'id': student.id, 'name': student.name, 'remind_statistics': student.remind_statistics})
        json.dump(data, f, indent=4)
        f.truncate()


def add_volunteer(volunteer: Volunteer):
    try:
        creds = connect_to_spreadsheets()
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
            
        access_token = creds.token
        spreadsheet_id = read_config("SAMPLE_SPREADSHEET_ID")
        range_name = f"B3:B{len(student_names) + 2}"
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{range_name}?valueInputOption=USER_ENTERED"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        body = {
            "values": [[name] for name in student_names]
        }

        original_getaddrinfo = socket.getaddrinfo
        socket.getaddrinfo = lambda *args, **kwargs: original_getaddrinfo(*args, **kwargs)[:1]
        response = requests.put(url, headers=headers, json=body, timeout=30)
        socket.getaddrinfo = original_getaddrinfo

        if response.status_code == 200:
            print("Volunteer names updated successfully in spreadsheet")
        else:
            print(f"Failed to update volunteer names: {response.status_code} - {response.text}")
        
    except Exception as e:
        print(f"Error adding volunteer: {e}")


def get_volunteer_index(volunteer: Volunteer):
    try:
        service = build('sheets', 'v4', credentials=connect_to_spreadsheets())
        result = get_sheets_values(read_config("SAMPLE_SPREADSHEET_ID"), read_config("VOLUNTEERS_NAME_RANGE"))
        
        student_names = result.get('values', [])
        final_names = []
        for name in student_names:
            if name:  # Check if name list is not empty
                final_names.append(name[0])
                
        for i in range(len(final_names)):
            if final_names[i] == volunteer.name:
                return i + 1
        return None
        
    except Exception as e:
        print(f"Error getting volunteer index: {e}")
        return None


def is_admin(user_id: int):
    admin_ids = read_config("ADMIN_IDS").split(", ")
    return str(user_id) in admin_ids


def get_volunteers():
    try:
        with open('data/Volunteers.json', 'r', encoding='UTF-8') as f:
            lines = json.load(f)
            result = []
            for line in lines:
                result.append(Volunteer([line.get("id"), line.get("name"), line.get("remind_statistics")]))
            return result
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"Error getting volunteers: {e}")
        return []


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


def get_current_week():
    threshold_day = datetime.datetime.now(pytz.timezone('Europe/Kiev')) + datetime.timedelta(days=-weekdays.get(read_config("THRESHOLD")))
    return threshold_day.isocalendar().week


def get_column_count(week = None):
    if week is not None:
        current_week = week
    else:
        current_week = get_current_week()
    return 2 - current_week % 2

def get_current_columns():
    first_week = 35
    first_column = 3
    current_week = get_current_week()
    columns = get_column_count()
    start_column = int(floor((current_week - first_week) * 1.5)) + first_column
    end_column = start_column + columns - 1
    return number_to_excel_column(start_column), number_to_excel_column(end_column)

def get_columns(week: int):
    first_week = 35
    first_column = 3
    current_week = week
    columns = get_column_count(week)
    start_column = int(floor((current_week - first_week) * 1.5)) + first_column
    end_column = start_column + columns - 1
    return number_to_excel_column(start_column), number_to_excel_column(end_column)


def volunteer_filled_statistics(volunteer):
    try:
        service = build('sheets', 'v4', credentials=connect_to_spreadsheets())
        start, end = get_current_columns()
        row = get_volunteer_index(volunteer)
        if row is None:
            return False
            
        range_name = f'Благовістя 2025!{start}{row + 2}:{end}{row + 2}'
        
        result = get_sheets_values(read_config("SAMPLE_SPREADSHEET_ID"), range_name)
        
        statistics = result.get('values', [])
        return bool(statistics and any(statistics[0]))

    except HttpError as err:
        print(f"HTTP Error checking statistics: {err}")
        return False
    except Exception as e:
        print(f"Error checking statistics: {e}")
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
    try:
        with open('data/texts.json', 'r', encoding='UTF-8') as file:
            content = json.load(file)
            return content.get(text, '')
    except FileNotFoundError:
        return ''
    except Exception as e:
        print(f"Error getting text: {e}")
        return ''


def arrange_keyboard(count, columns):
    result = []
    for i in range(count // columns):
        row = []
        for j in range(columns):
            row.append(KeyboardButton(i * columns + j))
        result.append(row)
    row = []
    for i in range(count % columns):
        row.append(KeyboardButton(count - count % columns + i))
    if len(row) > 0:
        result.append(row)
    return result


def update_texts():
    try:
        data = get_spreadsheets_data()
        if data is None:
            return
            
        questions = data.get("questions")
        with open("data/texts.json", "w", encoding='UTF-8') as file:
            data = questions.values
            dictionary = {}
            for row in data:
                if len(row) >= 2:  # Ensure row has at least 2 elements
                    dictionary[row[0]] = row[1]
            file.write(json.dumps(dictionary, indent=4, ensure_ascii=False))
    except Exception as e:
        print(f"Error updating texts: {e}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.message.chat_id, text='hello')


def select_random_question(text):
    questions = text.split(';;')
    return questions[random.randint(0, len(questions) - 1)]


def check_statistics_availability(id):
    if not volunteer_exists(id):
        return False, "NOT_REGISTERED"
    start, end = get_current_columns()
    if excel_column_to_number(start) < excel_column_to_number("C"):
        return False, "SEMESTER_NOT_STARTED"
    if excel_column_to_number(end) > excel_column_to_number("AF"):
        return False, "SEMESTER_OVER"
    return True, ""


async def question_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    id = update.message.chat_id
    available, message = check_statistics_availability(id)
    if not available:
        await context.bot.send_message(
            chat_id=id,
            text=select_random_question(get_text(message)).format(get_volunteer_name(id).split(" ")[1])
        )
        return ConversationHandler.END
    print(update.message.text)
    pattern = r"\d\d\.\d\d - \d\d\.\d\d \(\d.\):"
    if re.match(pattern, update.message.text):
        context.user_data['week'] = int(update.message.text.split('(')[-1].split(')')[0].strip())
    await context.bot.send_message(
        chat_id=id,
        text=select_random_question(get_text('QUESTION_1')),
        reply_markup=ReplyKeyboardMarkup(
            arrange_keyboard(9, 3),
            one_time_keyboard=True,
            resize_keyboard=True
        )
    )
    if get_column_count(context.user_data.get('week')) == 1:
        return EXIT_CONVERSATION_EARLY
    return ENTER_GOSPEL


async def question_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isnumeric():
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=select_random_question(get_text('VALIDATION_FAILED_NUMERIC')),
            reply_markup=ReplyKeyboardMarkup(
                arrange_keyboard(9, 3),
                one_time_keyboard=True,
                resize_keyboard=True
            )
        )
        return ENTER_GOSPEL
        
    context.user_data['searchers'] = update.message.text
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=select_random_question(get_text('QUESTION_2')),
        reply_markup=ReplyKeyboardMarkup(
            arrange_keyboard(9, 3),
            one_time_keyboard=True,
            resize_keyboard=True
        )
    )
    return EXIT_CONVERSATION


async def exit_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isnumeric():
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=select_random_question(get_text('VALIDATION_FAILED_NUMERIC')),
            reply_markup=ReplyKeyboardMarkup(
                arrange_keyboard(9, 3),
                one_time_keyboard=True,
                resize_keyboard=True
            )
        )
        return EXIT_CONVERSATION
        
    context.user_data['gospel'] = update.message.text
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=select_random_question(get_text('WRITING')),
        reply_markup=ReplyKeyboardRemove()
    )
    
    write_statistics_to_spreadsheets(
        Volunteer([str(update.message.chat_id), get_volunteer_name(str(update.message.chat_id)),
                   context.user_data['searchers'], context.user_data['gospel']]))
                   
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=select_random_question(get_text('STATISTICS_GATHERED')),
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


async def exit_conversation_early(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isnumeric():
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=select_random_question(get_text('VALIDATION_FAILED_NUMERIC')),
            reply_markup=ReplyKeyboardMarkup(
                arrange_keyboard(9, 3),
                one_time_keyboard=True,
                resize_keyboard=True
            )
        )
        return EXIT_CONVERSATION
        
    context.user_data['searchers'] = update.message.text
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=select_random_question(get_text('WRITING')),
        reply_markup=ReplyKeyboardRemove()
    )
    
    write_statistics_to_spreadsheets(
        Volunteer([str(update.message.chat_id), get_volunteer_name(str(update.message.chat_id)),
                   context.user_data['searchers'], 0]))
                   
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=select_random_question(get_text('STATISTICS_GATHERED')),
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def exit_previous_statistics_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isnumeric():
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=select_random_question(get_text('VALIDATION_FAILED_NUMERIC')),
            reply_markup=ReplyKeyboardMarkup(
                arrange_keyboard(9, 3),
                one_time_keyboard=True,
                resize_keyboard=True
            )
        )
        return EXIT_CONVERSATION
        
    context.user_data['gospel'] = update.message.text
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=select_random_question(get_text('WRITING')),
        reply_markup=ReplyKeyboardRemove()
    )
    
    write_statistics_to_spreadsheets(
        Volunteer([str(update.message.chat_id), get_volunteer_name(str(update.message.chat_id)), context.user_data['searchers'], 
        context.user_data['gospel']]), context.user_data.get('week')
    )
                   
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=select_random_question(get_text('STATISTICS_GATHERED')),
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


async def exit_previous_statistics_conversation_early(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isnumeric():
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=select_random_question(get_text('VALIDATION_FAILED_NUMERIC')),
            reply_markup=ReplyKeyboardMarkup(
                arrange_keyboard(9, 3),
                one_time_keyboard=True,
                resize_keyboard=True
            )
        )
        return EXIT_CONVERSATION
        
    context.user_data['searchers'] = update.message.text
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=select_random_question(get_text('WRITING')),
        reply_markup=ReplyKeyboardRemove()
    )
    
    write_statistics_to_spreadsheets(
        Volunteer([str(update.message.chat_id), get_volunteer_name(str(update.message.chat_id)),
                   context.user_data['searchers'], 0]), context.user_data.get('week'))
                   
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=select_random_question(get_text('STATISTICS_GATHERED')),
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END



async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data['id'] = str(update.message.chat_id)
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=select_random_question(get_text('GREETING'))
    )
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=select_random_question(get_text('ASK_NAME'))
    )
    return ENTER_NAME


async def enter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if len(update.message.text.split(" ")) != 2:
        await context.bot.send_message(
            chat_id=chat_id,
            text=select_random_question(get_text('VALIDATION_FAILED_NAME')).format(update.message.text)
        )
        return ENTER_NAME
        
    context.user_data['name'] = update.message.text
    await context.bot.send_message(
        chat_id=chat_id,
        text=select_random_question(get_text('WRITING'))
    )
    
    add_volunteer(Volunteer([int(context.user_data['id']), context.user_data['name']]))
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=select_random_question(get_text('REGISTRATION_COMPLETE')).format(
            get_volunteer_name(chat_id).split(" ")[-1])
    )
    
    restart_jobs(context.application.job_queue)
    return ConversationHandler.END


def write_statistics_to_spreadsheets(volunteer: Volunteer, week = None):
    try:
        creds = connect_to_spreadsheets()
        if not creds:
            print("No credentials for adding student")
            return
            
        access_token = creds.token
        spreadsheet_id = read_config("SAMPLE_SPREADSHEET_ID")

        start, end = get_current_columns()
        if week is None:
            start, end = get_current_columns()
        else:
            start, end = get_columns(week)
        row = get_volunteer_index(volunteer)
        if row is None:
            print(f"Volunteer {volunteer.name} not found in spreadsheet")
            return
            
        data = [volunteer.searchers]
        if start != end:
            data.append(volunteer.gospel)

        range_name = f"Благовістя 2025!{start}{row + 2}:{end}{row + 2}"
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{range_name}?valueInputOption=USER_ENTERED"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        body = {
            "values": [data]
        }

        original_getaddrinfo = socket.getaddrinfo
        socket.getaddrinfo = lambda *args, **kwargs: original_getaddrinfo(*args, **kwargs)[:1]
        response = requests.put(url, headers=headers, json=body, timeout=30)
        socket.getaddrinfo = original_getaddrinfo

        if response.status_code == 200:
            print(f"Volunteer {volunteer.name} successfully updated statistics in spreadsheet")
        else:
            print(f"Failed to update volunteer {volunteer.name}'s statistics: {response.status_code} - {response.text}")

        
    except Exception as e:
        print(f"Error writing statistics to spreadsheet: {e}")


async def end_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return ConversationHandler.END


async def reminder(context: ContextTypes.DEFAULT_TYPE):
    id = int(context.job.data[0])
    if len(context.job.data) > 1:
        order = context.job.data[1]
    else:
        order = 1
        
    if volunteer_filled_statistics(Volunteer([id, get_volunteer_name(id)])):
        await context.bot.send_message(
            chat_id=int(read_config("ADMIN_ID")),
            text=f"{get_volunteer_name(id)} already filled statistics"
        )
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
        await context.bot.send_message(
            chat_id=id,
            text=select_random_question(get_text(message)).format(get_volunteer_name(id).split(" ")[1]),
            reply_markup=ReplyKeyboardMarkup(
                keyboard,
                one_time_keyboard=True,
                resize_keyboard=True
            ),
            parse_mode=ParseMode.HTML
        )
        
        sleep(1)
        await context.bot.send_message(
            chat_id=int(read_config("ADMIN_ID")),
            text=f"reminded {get_volunteer_name(id)}"
        )
    except Exception as e:
        await context.bot.send_message(
            chat_id=int(read_config('ADMIN_ID')),
            text=f'chat {id} of {get_volunteer_name(id)} not found: {e}'
        )


async def spam_volunteers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        return
        
    order = 1
    if context.args and len(context.args) == 1:
        order = int(context.args[0])
        
    for id in get_volunteer_ids():
        context.job_queue.run_once(reminder, 0, data=[id, order])
        time.sleep(2)


async def spam_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    id = update.message.chat_id
    print(id)
    order = 3
    if context.args and len(context.args) == 1:
        order = int(context.args[0])
        
    if not is_admin(id):
        print("not admin")
        return
        
    time.sleep(2)
    context.job_queue.run_once(reminder, 0, data=[id, order])
    print(f"sent to {id}")


def restart_jobs(job_queue):
    delay_seconds = 0
    for job in job_queue.jobs():
        job.schedule_removal()
        
    for id in get_volunteer_ids():
        job_queue.run_daily(
            reminder,
            time=datetime.time(hour=17, minute=(delay_seconds // 60) % 60, second=delay_seconds % 60, tzinfo=pytz.timezone('Europe/Kyiv')),
            days=[0],
            data=[id, 1],
            name=str(id)
        )
        delay_seconds += 2
        
        job_queue.run_daily(
            reminder,
            time=datetime.time(hour=19, minute=(delay_seconds // 60) % 60, second=delay_seconds % 60, tzinfo=pytz.timezone('Europe/Kyiv')),
            days=[0],
            data=[id, 2],
            name=str(id)
        )
        delay_seconds += 2
        
        job_queue.run_daily(
            reminder,
            time=datetime.time(hour=21, minute=(delay_seconds // 60) % 60, second=delay_seconds % 60, tzinfo=pytz.timezone('Europe/Kyiv')),
            days=[0],
            data=[id, 3],
            name=str(id)
        )
        delay_seconds += 2


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton(select_random_question(get_text('FILL_STATISTICS')))],
        [KeyboardButton(get_text('SELECT_PREVIOUS_WEEK'))]
        ]
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=get_text('MENU'),
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True
        ),
        parse_mode=ParseMode.HTML
    )


def get_previous_weeks():
    current_time = datetime.datetime.now(pytz.timezone('Europe/Kiev'))
    threshold_day = current_time + datetime.timedelta(days=-weekdays.get(read_config("THRESHOLD")))
    current_week = threshold_day.isocalendar().week
    result = []
    weeks = []
    for week in range(35, current_week):
        week_str = f"{current_time.year}-W{week:02d}-1"
        monday = datetime.datetime.strptime(week_str, "%Y-W%W-%w").date()
        sunday = monday + datetime.timedelta(days=6)
        result.append(f"{monday.strftime("%d.%m")} - {sunday.strftime("%d.%m")} ({week})")
        weeks.append(week)
    return result, weeks


async def select_previous_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard_buttons = []
    volunteer = Volunteer([str(update.message.chat_id), get_volunteer_name(str(update.message.chat_id))])
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text="Сенкунду...",
        parse_mode=ParseMode.HTML
    )
    statistics = get_statistics_from_spreadsheets(volunteer, get_previous_weeks()[1])
    statistics_str = []
    i = 0
    while i < len(statistics):
        if statistics[i] is None or statistics[i] == '':
            statistics_str.append(f"Не заповнено")
            i += 1
            continue
        if i % 3 == 0:
            statistics_str.append(f"{statistics[i]} євангелій")
            i += 1
            continue
        if i % 3 == 1:
            statistics_str.append(f"{statistics[i]} євангелій, {statistics[i+1]} закликів")
            i += 2
        continue

    for i in range(len(get_previous_weeks()[1])):
        keyboard_buttons.append([KeyboardButton(
            f'{get_previous_weeks()[0][i]}: {statistics_str[i]}'
        )])
    await context.bot.send_message(
        chat_id=update.message.chat_id,
        text="Обери тиждень:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard_buttons,
            one_time_keyboard=True,
            resize_keyboard=True
        ),
        parse_mode=ParseMode.HTML
    )
    return ENTER_SEARCHERS


async def running_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.job_queue.jobs()) == 0:
        await context.bot.send_message(chat_id=update.message.chat_id, text='no running jobs')
        return
        
    reply = ''
    for job in context.job_queue.jobs():
        reply += f'{job.data}, {get_volunteer_name(job.data[0])}, {job.next_t}\n'
        if len(reply) > 3500:
            await context.bot.send_message(chat_id=update.message.chat_id, text=reply)
            reply = ''
            
    await context.bot.send_message(chat_id=update.message.chat_id, text=reply)


def main():
    update_texts()
    data = get_spreadsheets_data()
    if data:
        update_volunteers(data.get("volunteers", []))
    print("updated")
    
    application = Application.builder().token(read_config("BOT_TOKEN")).build()
    
    restart_jobs(application.job_queue)
    
    register_conversation_handler = ConversationHandler(
        entry_points=[CommandHandler('start', ask_name)],
        states={
            ENTER_NAME: [MessageHandler(filters.TEXT & (~ filters.COMMAND), enter_name)],
            ASK_NAME: [MessageHandler(filters.TEXT & (~ filters.COMMAND), ask_name)]
        },
        fallbacks=[]
    )
    
    gather_statistics_conversation_handler = ConversationHandler(
        entry_points=[
            CommandHandler('send_statistics', question_1),
            MessageHandler(filters.Text(get_text('FILL_STATISTICS')), question_1)
        ],
        states={
            ENTER_GOSPEL: [MessageHandler(filters.TEXT & (~ filters.COMMAND), question_2)],
            EXIT_CONVERSATION: [MessageHandler(filters.TEXT & (~ filters.COMMAND), exit_conversation)],
            EXIT_CONVERSATION_EARLY: [MessageHandler(filters.TEXT & (~ filters.COMMAND), exit_conversation_early)]
        },
        fallbacks=[CommandHandler('finish', end_conversation)]
    )
    
    gather_previous_statistics_conversation_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Text(get_text('SELECT_PREVIOUS_WEEK')), select_previous_week)
        ],
        states={
            ENTER_SEARCHERS: [MessageHandler(filters.TEXT & (~ filters.COMMAND), question_1)],
            ENTER_GOSPEL: [MessageHandler(filters.TEXT & (~ filters.COMMAND), question_2)],
            EXIT_CONVERSATION: [MessageHandler(filters.TEXT & (~ filters.COMMAND), exit_previous_statistics_conversation)],
            EXIT_CONVERSATION_EARLY: [MessageHandler(filters.TEXT & (~ filters.COMMAND), exit_previous_statistics_conversation_early)]
        },
        fallbacks=[CommandHandler('finish', end_conversation)]
    )
    
    application.add_handler(register_conversation_handler)
    application.add_handler(gather_statistics_conversation_handler)
    application.add_handler(gather_previous_statistics_conversation_handler)
    application.add_handler(CommandHandler("spam_admin", spam_admin))
    application.add_handler(CommandHandler("spam_volunteers", spam_volunteers))
    application.add_handler(CommandHandler("running_jobs", running_jobs))
    application.add_handler(CommandHandler("menu", show_menu))
    
    application.run_polling()


if __name__ == '__main__':
    main()