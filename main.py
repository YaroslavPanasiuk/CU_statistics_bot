import collections
import json
import os
import os.path
import random
import re
import time
import traceback
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
        self.nickname = values[2]
        self.searchers = values[3]
        self.gospel = values[4]
        self.teammate = values[5]


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
        questions = sheet.values().get(spreadsheetId=read_config("SAMPLE_SPREADSHEET_ID"),
                                       range=read_config('QUESTIONS_RANGE_NAME')).execute().get('values', [])
        if not questions:
            print('No questions found.')
            return
        questions_df = pd.DataFrame(questions)
        return {'questions': questions_df}

    except HttpError as err:
        print(err)


def add_volunteer(data: []):
    service = build('sheets', 'v4', credentials=connect_to_spreadsheets())
    students = service.spreadsheets().values().get(spreadsheetId=read_config("SAMPLE_SPREADSHEET_ID"),
                                                   range=read_config("VOLUNTEERS_RANGE_NAME")).execute()
    data_range = 'Аркуш2!A{0}:B{0}'.format(str(len(students.get('values', [])) + 1))
    range_body_values = {
        'value_input_option': 'USER_ENTERED',
        'data': [
            {
                'majorDimension': 'ROWS',
                'range': data_range,
                'values': [data]
            },
        ]}
    service.spreadsheets().values().batchUpdate(spreadsheetId=read_config("SAMPLE_SPREADSHEET_ID"),
                                                body=range_body_values).execute()


def get_text(text):
    file = open('texts.json', encoding='UTF-8')
    content = json.load(file)
    file.close()
    if content.get(text) is not None:
        return content.get(text)
    return ''


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


def echo(update, context):
    question = get_spreadsheets_data().get("questions")
    print(question)
    context.bot.send_message(chat_id=update.message.chat_id, text=str(question))


def select_random_question(text):
    questions = text.split('\n\n\n')
    return questions[random.randint(0, len(questions)-1)]


def ask_searchers(update, context):
    context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('ASK_SEARCHERS')))
    return ENTER_GOSPEL


def ask_gospel(update, context):
    context.user_data['searchers'] = update.message.text
    context.bot.send_message(chat_id=update.message.chat_id,
                             text=select_random_question(get_text('ASK_GOSPEL')))
    return ENTER_TEAMMATE


def ask_teammate(update, context):
    context.user_data['gospel'] = update.message.text
    context.bot.send_message(chat_id=update.message.chat_id,
                             text=select_random_question(get_text('ASK_TEAMMATE')))
    return EXIT_CONVERSATION


def exit_conversation(update, context):
    context.user_data['teammate'] = update.message.text
    write_statistics_to_spreadsheets()
    context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('STATISTICS_GATHERED')))
    end_conversation(update, context)


def ask_name(update, context):
    context.user_data.clear()
    context.user_data['id'] = update.message.chat_id
    context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('GREETING')))
    context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('ASK_NAME')))
    return ENTER_NAME


def enter_name(update, context):
    context.user_data['name'] = update.message.text
    add_volunteer([context.user_data['id'], context.user_data['name']])
    context.bot.send_message(chat_id=update.message.chat_id, text=select_random_question(get_text('STATISTICS_GATHERED')))
    end_conversation(update, context)


def write_statistics_to_spreadsheets():
    return None


def end_conversation(update, context):
    return ConversationHandler.END


def main():
    print("start")
    update_texts()
    updater = Updater(read_config("BOT_TOKEN"), use_context=True)
    dispatcher = updater.dispatcher
    #dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))
    register_conversation_handler = ConversationHandler(
        entry_points=[CommandHandler('start', ask_name)],
        states={ENTER_NAME: [MessageHandler(Filters.text & (~ Filters.command), enter_name)]},
        fallbacks=[]
    )
    gather_statistics_conversation_handler = ConversationHandler(
        entry_points=[MessageHandler(Filters.text(get_text('SEND_STATISTICS')), ask_searchers)],
        states={
            ENTER_GOSPEL: [MessageHandler(Filters.text & (~ Filters.command), ask_gospel)],
            ENTER_TEAMMATE: [MessageHandler(Filters.text & (~ Filters.command), ask_teammate)],
            EXIT_CONVERSATION: [MessageHandler(Filters.text & (~ Filters.command), exit_conversation)]
        },
        fallbacks=[CommandHandler('finish', exit_conversation)]
    )
    dispatcher.add_handler(register_conversation_handler)
    dispatcher.add_handler(gather_statistics_conversation_handler)
    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
