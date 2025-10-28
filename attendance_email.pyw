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

TEST_RUN = True
TEST_EMAIL = ''

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

def get_custody_contacts(student_dcid:int) -> list:
    """Function to take a student DCID number and return dictionary of the contacts with custody names and emails."""
    cur.execute('SELECT p.firstname, p.lastname, email.emailaddress FROM studentcontactassoc sca \
                LEFT JOIN studentcontactdetail scd ON scd.studentcontactassocid = sca.studentcontactassocid \
                LEFT JOIN personemailaddressassoc pemail ON pemail.personid = sca.personid \
                LEFT JOIN emailaddress email ON email.emailaddressid = pemail.emailaddressid \
                LEFT JOIN person p ON sca.personid = p.id \
                WHERE sca.studentdcid = :dcid AND scd.isactive = 1 AND scd.iscustodial = 1 AND pemail.isprimaryemailaddress = 1', dcid=student_dcid)
    custodians = cur.fetchall()
    print(f'DBUG: Number of contacts with custody and current emails for DCID {student_dcid}: {len(custodians)} - {custodians}')
    print(f'DBUG: Number of contacts with custody and current emails for DCID {student_dcid}: {len(custodians)} - {custodians}', file=log)
    return custodians if len(custodians) > 0 else None  # if we had results, return the list of tuples, otherwise just return None

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
                    cur.execute('SELECT stu.student_number, stu.id, stu.dcid, stu.first_name, stu.last_name, absent.auto_unex_notified_1, absent.auto_unex_notified_2, absent.auto_unex_notified_3, ext.hls_requestedlang \
                                FROM students stu LEFT JOIN u_chronicabsenteeism absent on stu.dcid = absent.studentsdcid LEFT JOIN u_def_ext_students0 ext ON stu.dcid = ext.studentsdcid \
                                WHERE stu.enroll_status = 0 AND stu.schoolid = :school', school=schoolid)
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
                            requestedLanguage = str(student[8])
                            # would need to add more custom fields for notifications if there were more than 3 thresholds
                            try:
                                # do the query of attendance table for the unexcused day code
                                cur.execute("SELECT studentid, schoolid, dcid, att_date FROM attendance WHERE ATT_MODE_CODE = 'ATT_ModeDaily' AND studentid = :student AND attendance_codeid = :code AND YEARID = :year ORDER BY att_date", student=stuID, code=attendanceCodeMap.get(schoolid), year=termYear)
                                entries = cur.fetchall()
                                if len(entries) > 0:
                                    try:
                                        print(f'DBUG: Student {stuNum} - {firstName} {lastName} has {len(entries)} unexcused absence(s) in year code {termYear}')
                                        print(f'DBUG: Student {stuNum} - {firstName} {lastName} has {len(entries)} unexcused absence(s) in year code {termYear}', file=log)
                                        for entry in entries:
                                            print(f'DBUG: {stuNum} had an unexcused absence at building {entry[1]} on {entry[3].strftime("%m/%d/%y")}')
                                            print(f'DBUG: {stuNum} had an unexcused absence at building {entry[1]} on {entry[3].strftime("%m/%d/%y")}', file=log)
                                        contactsToEmail = get_custody_contacts(stuDCID)  # get the contacts with custody
                                        # toEmail = ''  # override to field
                                        if len(entries) >= NOTIFY_THRESHOLDS[2] and not thirdNotification:  # if they are over the max threshold and havent been notified
                                            if contactsToEmail:
                                                for contact in contactsToEmail:
                                                    try:
                                                        contactFirstLast = f'{contact[0]} {contact[1]}'  # get their name in one string
                                                        toEmail = str(contact[2])
                                                        print(f'INFO: {stuNum} has reached the last threshold of {NOTIFY_THRESHOLDS[2]} with {len(entries)} absences, sending email to contact {contactFirstLast} at {toEmail}, requested language is {requestedLanguage}')
                                                        print(f'INFO: {stuNum} has reached the last threshold of {NOTIFY_THRESHOLDS[2]} with {len(entries)} absences, sending email to contact {contactFirstLast} at {toEmail}, requested language is {requestedLanguage}', file=log)
                                                        mime_message = EmailMessage()  # create an email message object
                                                        # define headers
                                                        if TEST_RUN:
                                                            mime_message['To'] = TEST_EMAIL
                                                        else:
                                                            mime_message['To'] = toEmail
                                                        if requestedLanguage == 'Spanish':
                                                            mime_message['Subject'] = 'Importante: Preocupación por la Asistencia de su Estudiante'  # subject line of the email
                                                            mime_message.set_content(f'Estimado/a {contactFirstLast}:\nLe escribimos para expresar nuestra preocupación con respecto a la asistencia de su estudiante. Hasta la fecha, su estudiante tiene {len(entries)} ausencias sin justificación, lo que está afectando su capacidad para mantenerse al día con las tareas y conservar su progreso académico.\n\nLa Junta de Educación del Estado de Illinois (ISBE, por sus siglas en inglés) advierte que el ausentismo crónico—faltar el 10% o más de los días escolares—incrementa drásticamente el riesgo de fracaso académico y reduce la probabilidad de graduarse a tiempo. Con siete ausencias sin justificación, su estudiante corre el riesgo de alcanzar este umbral preocupante.\n\nLa asistencia regular es fundamental para que su estudiante reciba la instrucción y el apoyo esenciales. Continuar con este nivel de ausencias coloca a su estudiante en un riesgo significativo de quedarse atrasado, lo que puede tener consecuencias a largo plazo.\n\nLe instamos a que atienda este problema de asistencia de inmediato. Por favor, comuníquese con nosotros si existe alguna barrera que impida que su estudiante asista a la escuela para que podamos trabajar juntos en encontrar soluciones.\n\nGracias por su inmediata atención y cooperación.')
                                                        else:
                                                            mime_message['Subject'] = f'Attention Required: {len(entries)} Unexcused Absences'
                                                            mime_message.set_content(f'Dear {contactFirstLast},\nWe are writing to express concern regarding your student\'s attendance. To date, your student has {len(entries)} unexcused absences, which is impacting their ability to keep up with coursework and maintain academic progress.\n\nThe Illinois State Board of Education (ISBE) warns that chronic absenteeism—missing 10% or more of school days—dramatically increases the risk of academic failure and decreases the likelihood of on-time graduation. At seven unexcused absences, your student is in danger of reaching this concerning threshold.\n\nRegular attendance is critical to ensure your student receives essential instruction and support. Continued absences at this level put your student at significant risk of falling behind, which can have long-term consequences.\n\nWe urge you to address this attendance issue immediately. Please contact the administration if there are any barriers preventing your student from attending school so we can work together to find solutions.\n\nThank you for your immediate attention and cooperation.')
                                                        encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()
                                                        create_message = {'raw': encoded_message}
                                                        send_message = (service.users().messages().send(userId="me", body=create_message).execute())  # send the email
                                                        print(f'DBUG: Email sent, message ID: {send_message["id"]}')  # print out resulting message Id
                                                        print(f'DBUG: Email sent, message ID: {send_message["id"]}', file=log)
                                                        if not TEST_RUN:
                                                            ps_update_custom_field('u_chronicabsenteeism', 'auto_unex_notified_3', stuDCID, True)
                                                    except HttpError as er:   # catch Google API http errors, get the specific message and reason from them for better logging
                                                        status = er.status_code
                                                        details = er.error_details[0]  # error_details returns a list with a dict inside of it, just strip it to the first dict
                                                        print(f'ERROR {status} from Google API while sending email to {toEmail} about student {stuNum} past 3rd threshold with {len(entries)} absences: {details["message"]}. Reason: {details["reason"]}')
                                                        print(f'ERROR {status} from Google API while sending email to {toEmail} about student {stuNum} past 3rd threshold with {len(entries)} absences: {details["message"]}. Reason: {details["reason"]}', file=log)
                                                    except Exception as er:
                                                        print(f'ERROR while trying to send email to {toEmail} about student {stuNum} past 3rd threshold with {len(entries)} absences: {er}')
                                                        print(f'ERROR while trying to send email to {toEmail} about student {stuNum} past 3rd threshold with {len(entries)} absences: {er}', file=log)
                                            else:
                                                print(f'ERROR: No contacts with custody found for student {stuNum}, no emails sent')
                                                print(f'ERROR: No contacts with custody found for student {stuNum}, no emails sent', file=log)
                                        elif NOTIFY_THRESHOLDS[1] <= len(entries) <= NOTIFY_THRESHOLDS[2] and not secondNotification:  # if they are at least the second threshold but still less than the third and they havent been notified yet
                                            if contactsToEmail:
                                                for contact in contactsToEmail:
                                                    try:
                                                        contactFirstLast = f'{contact[0]} {contact[1]}'  # get their name in one string
                                                        toEmail = str(contact[2])
                                                        print(f'INFO: {stuNum} has reached the second threshold of {NOTIFY_THRESHOLDS[1]} with {len(entries)} absences, sending email to contact {contactFirstLast} at {toEmail}, requested language is {requestedLanguage}')
                                                        print(f'INFO: {stuNum} has reached the second threshold of {NOTIFY_THRESHOLDS[1]} with {len(entries)} absences, sending email to contact {contactFirstLast} at {toEmail}, requested language is {requestedLanguage}', file=log)
                                                        mime_message = EmailMessage()  # create an email message object
                                                        if TEST_RUN:
                                                            mime_message['To'] = TEST_EMAIL
                                                        else:
                                                            mime_message['To'] = toEmail
                                                        if requestedLanguage == 'Spanish':
                                                            mime_message['Subject'] = 'Importante: Preocupación por la Asistencia de su Estudiante'  # subject line of the email
                                                            mime_message.set_content(f'Estimado/a {contactFirstLast}:\nNos comunicamos con usted para informarle que su estudiante ha acumulado {len(entries)} ausencias sin justificación. La asistencia regular es esencial para el éxito académico y las oportunidades futuras de su estudiante.\n\nSegún la Junta de Educación del Estado de Illinois (ISBE, por sus siglas en inglés), faltar tan solo el 10% del año escolar—aproximadamente 18 días—pone a los estudiantes en un riesgo serio de quedarse atrasado académicamente y reduce sus posibilidades de graduarse a tiempo. Con cinco ausencias sin justificación, su estudiante ya presenta señales tempranas de problemas de asistencia que pueden afectar su progreso.\n\nCada día escolar ofrece instrucción y oportunidades de interacción social que no se pueden recuperar completamente. Le instamos encarecidamente a que trabaje con su estudiante para mejorar su asistencia de inmediato. Si existen obstáculos que impidan que su estudiante asista regularmente a la escuela, comuníquese con nosotros lo antes posible para que podamos brindarle apoyo.\n\nGracias por su pronta atención a este asunto tan importante.')
                                                        else:
                                                            mime_message['Subject'] = f'Important: Attendance Concern - {len(entries)} Unexcused Absences'  # subject line of the email
                                                            mime_message.set_content(f'Dear {contactFirstLast},\nWe are reaching out to inform you that your student has accumulated {len(entries)} unexcused absences. Regular attendance is essential for your student\'s academic success and future opportunities.\n\nAccording to the Illinois State Board of Education (ISBE), missing just 10% of the school year—approximately 18 days—puts students at serious risk of falling behind academically and reduces their chances of graduating on time. At five unexcused absences, your student is already showing early signs of attendance concerns that can impact their progress.\n\nEvery school day provides critical instruction and social interaction that cannot be fully recovered. We strongly urge you to work with your student to improve attendance immediately. If there are any obstacles preventing your student from attending school regularly, please contact the administration as soon as possible so we can provide support.\n\nThank you for your prompt attention to this important matter.')
                                                        encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()
                                                        create_message = {'raw': encoded_message}
                                                        send_message = (service.users().messages().send(userId="me", body=create_message).execute())  # send the email
                                                        print(f'DBUG: Email sent, message ID: {send_message["id"]}')  # print out resulting message Id
                                                        print(f'DBUG: Email sent, message ID: {send_message["id"]}', file=log)
                                                        if not TEST_RUN:
                                                            ps_update_custom_field('u_chronicabsenteeism', 'auto_unex_notified_2', stuDCID, True)
                                                    except HttpError as er:   # catch Google API http errors, get the specific message and reason from them for better logging
                                                        status = er.status_code
                                                        details = er.error_details[0]  # error_details returns a list with a dict inside of it, just strip it to the first dict
                                                        print(f'ERROR {status} from Google API while sending email to {toEmail} about student {stuNum} past 2nd threshold with {len(entries)} absences: {details["message"]}. Reason: {details["reason"]}')
                                                        print(f'ERROR {status} from Google API while sending email to {toEmail} about student {stuNum} past 2nd threshold with {len(entries)} absences: {details["message"]}. Reason: {details["reason"]}', file=log)
                                                    except Exception as er:
                                                        print(f'ERROR while trying to send email to {toEmail} about student {stuNum} past 2nd threshold with {len(entries)} absences: {er}')
                                                        print(f'ERROR while trying to send email to {toEmail} about student {stuNum} past 2nd threshold with {len(entries)} absences: {er}', file=log)
                                            else:
                                                print(f'ERROR: No contacts with custody found for student {stuNum}, no emails sent')
                                                print(f'ERROR: No contacts with custody found for student {stuNum}, no emails sent', file=log)
                                        elif NOTIFY_THRESHOLDS[0] <= len(entries) <= NOTIFY_THRESHOLDS[1] and not firstNotification:  # if they are at least first threshold but less than second and havent been notified yet
                                            if contactsToEmail:
                                                for contact in contactsToEmail:
                                                    try:
                                                        contactFirstLast = f'{contact[0]} {contact[1]}'  # get their name in one string
                                                        toEmail = str(contact[2])
                                                        print(f'INFO: {stuNum} has reached the first threshold of {NOTIFY_THRESHOLDS[0]} with {len(entries)} absences, sending email to contact {contactFirstLast} at {toEmail}, requested language is {requestedLanguage}')
                                                        print(f'INFO: {stuNum} has reached the first threshold of {NOTIFY_THRESHOLDS[0]} with {len(entries)} absences, sending email to contact {contactFirstLast} at {toEmail}, requested language is {requestedLanguage}', file=log)
                                                        mime_message = EmailMessage()  # create an email message object
                                                        if TEST_RUN:
                                                            mime_message['To'] = TEST_EMAIL
                                                        else:
                                                            mime_message['To'] = toEmail
                                                        if requestedLanguage == 'Spanish':
                                                            mime_message['Subject'] = 'Importante: Preocupación por la Asistencia de su Estudiante'  # subject line of the email
                                                            mime_message.set_content(f'Estimado/a {contactFirstLast}:\nLe escribimos para informarle que su estudiante ha estado ausente sin justificación durante {len(entries)} días de clases. La asistencia diaria y constante es fundamental para el éxito académico, especialmente a nivel de escuela secundaria. Según la Junta de Educación del Estado de Illinois (ISBE, por sus siglas en inglés), los estudiantes que asisten regularmente a la escuela tienen más probabilidades de graduarse a tiempo y obtener un mejor rendimiento académico.\n\nLos datos de la ISBE muestran que los estudiantes que faltan el 10% o más de los días escolares (aproximadamente 18 días en un año escolar) tienen un riesgo significativamente mayor de quedarse atrasado en sus estudios. Cada día cuenta, y asistir a la escuela todos los días ayuda a desarrollar habilidades esenciales, mantener la participación en clase y garantizar que su estudiante se mantenga encaminado.\n\nPor favor, motive a su estudiante a asistir a la escuela todos los días para apoyar su progreso educativo. Si existe alguna preocupación o desafío que esté afectando la asistencia, no dude en comunicarse con nosotros para que podamos ayudarle.\n\nGracias por su atención a este asunto tan importante.')
                                                        else:
                                                            mime_message['Subject'] = 'Important: Attendance Concern for Your Student'  # subject line of the email
                                                            mime_message.set_content(f'Dear {contactFirstLast},\nWe are writing to inform you that your student has been unexcused for {len(entries)} days of school. Consistent daily attendance is critical to academic success, especially at the high school level. According to the Illinois State Board of Education (ISBE), students who attend school regularly are more likely to graduate on time and perform better academically.\n\nISBE data shows that students who miss 10% or more of school (about 18 days in a school year) are at a significantly higher risk of falling behind in their studies. Every day counts, and attending school daily helps build essential skills, maintain classroom engagement, and ensure your student stays on track.\n\nPlease encourage your student to attend school every day to support their educational progress. If there are any concerns or challenges affecting attendance, do not hesitate to reach out to the administration so we can assist.\n\nThank you for your attention to this important matter.')
                                                        encoded_message = base64.urlsafe_b64encode(mime_message.as_bytes()).decode()
                                                        create_message = {'raw': encoded_message}
                                                        send_message = (service.users().messages().send(userId="me", body=create_message).execute())  # send the email
                                                        print(f'DBUG: Email sent, message ID: {send_message["id"]}')  # print out resulting message Id
                                                        print(f'DBUG: Email sent, message ID: {send_message["id"]}', file=log)
                                                        if not TEST_RUN:
                                                            ps_update_custom_field('u_chronicabsenteeism', 'auto_unex_notified_1', stuDCID, True)
                                                    except HttpError as er:   # catch Google API http errors, get the specific message and reason from them for better logging
                                                        status = er.status_code
                                                        details = er.error_details[0]  # error_details returns a list with a dict inside of it, just strip it to the first dict
                                                        print(f'ERROR {status} from Google API while sending email to {toEmail} about student {stuNum} past 1st threshold with {len(entries)} absences: {details["message"]}. Reason: {details["reason"]}')
                                                        print(f'ERROR {status} from Google API while sending email to {toEmail} about student {stuNum} past 1st threshold with {len(entries)} absences: {details["message"]}. Reason: {details["reason"]}', file=log)
                                                    except Exception as er:
                                                        print(f'ERROR while trying to send email to {toEmail} about student {stuNum} past 1st threshold with {len(entries)} absences: {er}')
                                                        print(f'ERROR while trying to send email to {toEmail} about student {stuNum} past 1st threshold with {len(entries)} absences: {er}', file=log)
                                            else:
                                                print(f'ERROR: No contacts with custody found for student {stuNum}, no emails sent')
                                                print(f'ERROR: No contacts with custody found for student {stuNum}, no emails sent', file=log)

                                    except Exception as er:
                                        print(f'ERROR while doing day counting and intial unexcused notification decision for {stuNum}: {er}')
                                        print(f'ERROR while doing day counting and intial unexcused notification decision for {stuNum}: {er}', file=log)
                            except Exception as er:
                                print(f'ERROR while finding absences for student {stuNum}: {er}')
                                print(f'ERROR while finding absences for student {stuNum}: {er}', file=log)
                        except Exception as er:
                            print(f'ERROR while processing student {student[0]}: {er}')
                            print(f'ERROR while processing student {student[0]}: {er}', file=log)
