"""Script to send emails to guardians when students reach certain unexcused absence or mental health thresholds.

https://github.com/Philip-Greyson/D118-Attendance-Email

Needs the google-api-python-client, google-auth-httplib2 and the google-auth-oauthlib:
pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib
also needs oracledb: pip install oracledb --upgrade
"""

import base64
import json
import os  # needed for environement variable reading
import sys
from datetime import *
from email.message import EmailMessage

# importing module
import acme_powerschool
import oracledb  # needed for connection to PowerSchool server (ordcle database)
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# setup db connection
DB_UN = os.environ.get('POWERSCHOOL_READ_USER')  # username for read-only database user
DB_PW = os.environ.get('POWERSCHOOL_DB_PASSWORD')  # the password for the database account
DB_CS = os.environ.get('POWERSCHOOL_PROD_DB')  # the IP address, port, and database name to connect to
print(f'DBUG: Database Username: {DB_UN} |Password: {DB_PW} |Server: {DB_CS}')  # debug so we can see where oracle is trying to connect to/with

d118_client_id = os.environ.get("POWERSCHOOL_API_ID")
d118_client_secret = os.environ.get("POWERSCHOOL_API_SECRET")

# Google API Scopes that will be used. If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/gmail.compose']

ATTENDANCE_CODE = 'UN'
SCHOOL_IDS = [5]  # the IDs of the schools it will run at
NOTIFY_THRESHOLDS = [3,5,7]  # number of absences it will notify at

def ps_update_custom_field(table: str, field: str, dcid: int, value: any) -> str:
    """Function to do the update of a custom field in a student extension table, so that the large json does not need to be used every time an update is needed elsewhere."""
    # print(f'DBUG: table {table}, field {field}, student DCID {dcid}, value {value}')
    try:
        ps = acme_powerschool.api('d118-powerschool.info', client_id=d118_client_id, client_secret=d118_client_secret)  # create ps object via the API to do requests on
        data = {
            'students' : {
                'student': [{
                    '@extensions': table,
                    'id' : str(dcid),
                    'client_uid' : str(dcid),
                    'action' : 'UPDATE',
                    '_extension_data': {
                        '_table_extension': [{
                            'name': table,
                            '_field': [{
                                'name': field,
                                'value': value
                            }]
                        }]
                    }
                }]
            }
        }
        result = ps.post(f'ws/v1/student?extensions={table}', data=json.dumps(data))
        statusCode = result.json().get('results').get('result').get('status')
    except Exception as er:
        print(f'ERROR while trying to update custom field {field} in table {table} for student DCID {dcid}: {er}')
        print(f'ERROR while trying to update custom field {field} in table {table} for student DCID {dcid}: {er}')
        return 'ERROR'
    if statusCode != 'SUCCESS':
        print(f"ERROR: Could not update field {field}  in table {table} for student DCID {dcid}, status {result.json().get('results').get('result')}")
        print(f"ERROR: Could not update field {field}  in table {table} for student DCID {dcid}, status {result.json().get('results').get('result')}", file=log)
    else:
        print(f'DBUG: Successfully updated field {field} in table {table} for student DCID {dcid} to {value}')
        print(f'DBUG: Successfully updated field {field} in table {table} for student DCID {dcid} to {value}', file=log)
    return statusCode

if __name__ == '__main__':
    with open('attendance_notification_log.txt', 'w') as log:
        startTime = datetime.now()
        startTime = startTime.strftime('%H:%M:%S')
        print(f'INFO: Execution started at {startTime}')
        print(f'INFO: Execution started at {startTime}', file=log)
        creds = None
        # The file token.json stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            # Save the credentials for the next run
            with open('token.json', 'w') as token:
                token.write(creds.to_json())

        service = build('gmail', 'v1', credentials=creds)  # create the Google API service with just gmail functionality

        attendanceCodeMap = {}  # start with an empty dictionary that will store the actual attendance codes for each building

        # create the connecton to the PowerSchool database
        with oracledb.connect(user=DB_UN, password=DB_PW, dsn=DB_CS) as con:
            with con.cursor() as cur:  # start an entry cursor
                print(f'INFO: Connection established to PS database on version: {con.version}')
                print(f'INFO: Connection established to PS database on version: {con.version}', file=log)

                for schoolid in SCHOOL_IDS:
                    # get the term year number which is used to search the attendance codes table for the correct code to pass to attendance
                    try:
                        today = datetime.now()  # get todays date and store it for finding the correct term later
                        termYear = None
                        cur.execute("SELECT firstday, lastday, yearid FROM terms WHERE schoolid = :school AND isyearrec = 1 ORDER BY dcid DESC", school=schoolid)  # get a list of terms for the current school, filtering to only yearlong terms
                        terms = cur.fetchall()
                        for term in terms:  # go through every term
                            termStart = term[0]
                            termEnd = term[1]
                            #compare todays date to the start and end dates
                            if ((termStart < today) and (termEnd > today)):
                                termYear = str(term[2])
                                print(f'DBUG: Found current year ID of {termYear}')
                    except Exception as er:
                        print(f'ERROR while trying to find termyear for todays date of {today}: {er}')
                        print(f'ERROR while trying to find termyear for todays date of {today}: {er}', file=log)
                    if not termYear:  # if we could not find a term year that contained todays date
                        print('WARN: Could not find a matching term year for todays date to get attendance from, ending mental health notification execution')
                        print('WARN: Could not find a matching term year for todays date to get attendance from, ending mental health notification execution', file=log)
                        sys.exit()  # end the script

                    # get a map of school code to attendance codes from the attendance_code table
                    try:
                        cur.execute('SELECT schoolid, id FROM attendance_code WHERE yearid = :year and att_code = :code', year=termYear, code=ATTENDANCE_CODE)
                        codes = cur.fetchall()
                        for code in codes:
                            attendanceCodeMap.update({code[0]: code[1]})  # add the school:id map to the dictionary
                        print(f'DBUG: attendance code IDs: {attendanceCodeMap}')
                        print(f'DBUG: attendance code IDs: {attendanceCodeMap}', file=log)
                    except Exception as er:
                        print(f'ERROR: Could not generated code map for year {termYear}, ending execution: {er}')
                        print(f'ERROR: Could not generated code map for year {termYear}, ending execution: {er}', file=log)
                        sys.exit()  # end the script

                    # get all the active students in the building
                    cur.execute('SELECT stu.student_number, stu.id, stu.dcid, stu.first_name, stu.last_name, absent.auto_unex_notified_1, absent.auto_unex_notified_2, absent.auto_unex_notified_3 FROM students stu LEFT JOIN u_chronicabsenteeism absent on stu.dcid = absent.studentsdcid WHERE stu.enroll_status = 0 AND stu.schoolid = :school', school=schoolid)
                    students = cur.fetchall()
                    for student in students:  # start going through students one at a time
                        try:
                            stuNum = str(int(student[0]))  # remove the extra trailing .0
                            stuID = int(student[1])  # ps internal ID number, used in attendance table
                            stuDCID = int(student[2])
                            firstName = str(student[3]).title()  # have it be normal capitalization, not all caps like in PS
                            lastName = str(student[4]).title()  # have it be normal capitalization, not all caps like in PS
                            firstNotification = True if student[5] == 1 else False  # get whether we have already sent the first notification email via the custom field boolean in PS
                            secondNotification = True if student[6] == 1 else False
                            thirdNotification = True if student[7] == 1 else False
                            # would need to add more custom fields for notifications if there were more than 3 thresholds
                            try:
                                # do the query of attendance table for the unexcused day code
                                cur.execute("SELECT studentid, schoolid, dcid, att_date FROM attendance WHERE ATT_MODE_CODE = 'ATT_ModeDaily' AND studentid = :student AND attendance_codeid = :code AND YEARID = :year ORDER BY att_date", student=stuID, code=attendanceCodeMap.get(schoolid), year=termYear)
                                entries = cur.fetchall()
                                if len(entries) > 0:
                                    try:
                                        print(f'DBUG: Student {stuNum} has {len(entries)} unexcused absence(s) in year code {termYear}')
                                        print(f'DBUG: Student {stuNum} has {len(entries)} unexcused absence(s) in year code {termYear}', file=log)
                                        for entry in entries:
                                            print(f'DBUG: {stuNum} had an unexcused absence at building {entry[1]} on {entry[3].strftime("%m/%d/%y")}')
                                            print(f'DBUG: {stuNum} had an unexcused absence at building {entry[1]} on {entry[3].strftime("%m/%d/%y")}', file=log)
                                    except Exception as er:
                                        print(f'ERROR while doing day counting and intial unexcused notification decision for {stuNum}: {er}')
                                        print(f'ERROR while doing day counting and intial unexcused notification decision for {stuNum}: {er}', file=log)
                            except Exception as er:
                                print(f'ERROR while finding absences for student {stuNum}: {er}')
                                print(f'ERROR while finding absences for student {stuNum}: {er}', file=log)
                        except Exception as er:
                            print(f'ERROR while processing student {student[0]}: {er}')
                            print(f'ERROR while processing student {student[0]}: {er}', file=log)