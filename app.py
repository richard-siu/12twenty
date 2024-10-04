# %% [markdown]
# # ODS to 12Twenty - Python Version
# 
# ## Overview
# This program uses python, the HTTP requests library, "requests", and the data analysis/manipulation library, "pandas".  It is currently in the form of a Jupyter Notebook.  The notebook approach is very handy in development since you can run "cells" one at a time and check the data as you go.  In production, this code would run as a simple python script.  Currently, it uses a static version of the ODS data, imported to our DEV SQL Server via SSIS.  However, there is no reason that the python script couldn't just query ODS directly, eliminating the need for SSIS. 

# %%
#install and import required libraries
# !pip3 install requests
# !pip3 install pyodbc
import json, requests, pandas as pd, pyodbc

# %%
#authenticate with 12Twenty
base_url = 'https://siu.admin.12twenty.com/api'
auth_key = open('api_key.txt', 'r').read()
auth_url = base_url + '/client/generateAuthenticationToken?key=' + auth_key

bearer_token = requests.get(auth_url).text
#set authorization header for future requests
auth_header = {'Authorization': 'Bearer ' + bearer_token}

# %%
#pull first page of json students from 12Twenty
students_url = base_url + '/V2/students'
students_params = {'PageSize' : '500'}
twelve_twenty_students_json = requests.get(students_url, headers=auth_header, params=students_params)
#print(json.dumps(twelve_twenty_students_json.json(), indent=2))

# %%
#convert json to a pandas dataframe
twelve_twenty_students_json = json.loads(twelve_twenty_students_json.text)
twelve_twenty_students_page = pd.json_normalize(twelve_twenty_students_json['Items'])
twelve_twenty_students_page.head()

# %%
#create array to store paginated 12Twenty data
twelve_twenty_students = []
twelve_twenty_students.append(twelve_twenty_students_page)
#iterate through all pages of 12Twenty students and assemble array of dataframes
num_pages = twelve_twenty_students_json['NumberOfPages']
for page in range(2, num_pages + 1):
    students_params = {'PageSize' : '500', 'PageNumber': page}
    twelve_twenty_students_json = json.loads(requests.get(students_url, headers=auth_header, params=students_params).text)
    twelve_twenty_students_page = pd.json_normalize(twelve_twenty_students_json['Items'])
    twelve_twenty_students.append(twelve_twenty_students_page)

# %%
#convert array of data frames into one master data frame
twelve_twenty_students = pd.concat(twelve_twenty_students)

print(twelve_twenty_students.count())

# %%
#remove unnecessary columns and rename remaining ones to match ODS columns

#print(twelve_twenty_students.columns.tolist())
columnMapping = {'Id':'12TwentyId',
                 'FirstName':'FirstName',
                 'MiddleName':'MiddleName',
                 'LastName' : 'LastName',
                 'EmailAddress' : 'EmailAddress',
                 'IsEnrolled': 'IsEnrolled',
                 'StudentId': 'StudentId',
                 'College.Name': 'College',
                 'Program.Name': 'Program',
                 'GraduationYearId' : 'GraduationYear',
                 'GraduationTerm' : 'GraduationTerm',
                 'DegreeLevel.Name' : 'DegreeLevel',
                 'CustomAttributeValues.custom_attribute_10888805132042': 'AppliedForGraduation'
}

for column in twelve_twenty_students.columns:
    if column not in columnMapping.keys():
        twelve_twenty_students.drop(column, axis=1, inplace=True)

twelve_twenty_students.rename(columns = columnMapping, inplace='True')

#set primary key
twelve_twenty_students.set_index('StudentId')

twelve_twenty_students.head()


# %%
#set up connection to SQL db
sql_connection = pyodbc.connect("Driver={SQL Server};"
                      "Server=itapp-ssis-dev;"
                      "Database=MableySandbox;"
                      "Trusted_Connection=yes;")


# %%
#query ODS
ods_query = "SELECT * FROM TwelveTwentyStudents;"
ods_students = pd.read_sql(ods_query,sql_connection)
#add action and reporting columns
ods_students.insert(0, 'ActionNeeded', 'None')
ods_students.insert(len(ods_students.columns), 'Result', '')
ods_students.insert(len(ods_students.columns), 'Message', '')
#strip year from graduation term
ods_students['GraduationTerm'] = ods_students['GraduationTerm'].str.replace('[^a-zA-Z]*', '', regex=True)
#set primary key
ods_students.set_index('StudentId')

# %%
#if ODS student ID is not found in 12Twenty list, mark it for Insert
ods_students.loc[~ods_students['StudentId'].isin(twelve_twenty_students['StudentId']), 'ActionNeeded'] = 'Insert'
ods_students.loc[ods_students['ActionNeeded'] == 'Insert']

# %%
#left join ODS and 12Twenty dataframes with suffixes for data origin on column names
merged_students = pd.merge(ods_students, twelve_twenty_students, on ='StudentId', how='left', suffixes=['_ODS', '_12Twenty'])
merged_students.head()

# %%
#mark existing 12Twenty students for update where data differs from ODS to 12Twenty
merged_students.loc[(merged_students['ActionNeeded'] != 'Insert') &
                    (
                    (merged_students['EmailAddress_ODS'] != merged_students['EmailAddress_12Twenty']) |
                    (merged_students['FirstName_ODS']!= merged_students['FirstName_12Twenty']) |
                    (merged_students['LastName_ODS']!= merged_students['LastName_12Twenty']) |
                    (merged_students['DegreeLevel_ODS']!= merged_students['DegreeLevel_12Twenty']) |
                    (merged_students['GraduationYear_ODS']!= merged_students['GraduationYear_12Twenty']) |
                    (merged_students['GraduationTerm_ODS']!= merged_students['GraduationTerm_12Twenty']) |
                    (merged_students['AppliedForGrad'] != merged_students['AppliedForGraduation']) 
                    ), 'ActionNeeded'] = 'Update'
merged_students.loc[merged_students['ActionNeeded'] == 'Update'].head()


# %%
#display final counts and data table
print(f"Students to Insert: {merged_students.loc[merged_students['ActionNeeded'] == 'Insert']['StudentId'].count()}") 
print(f"Students to Update: {merged_students.loc[merged_students['ActionNeeded'] == 'Update']['StudentId'].count()}") 
with pd.option_context('display.max_rows', 25, 'display.max_columns', None): 
    display(merged_students.loc[merged_students['ActionNeeded'] != 'None'].sort_values(by=['ActionNeeded', 'LastName_ODS', 'FirstName_ODS'], ascending=[False, True, True]))

# %% [markdown]
# ## Pushing Data to 12Twenty
# 
# From here, we would simply filter the merged list of students and use requests to POST/PATCH the students to insert/update back to 12Twenty using the _ODS columns.
# 
# ## Logging/Reporting
# 
# We would log the result and any error message to the "Result" and "Message" columns of the dataframe, then use it to generate a nice HTML table for email reporting.  We could generate text logs, but there are also [python libraries for Splunk reporting](https://dev.splunk.com/enterprise/docs/devtools/python/sdk-python/).
# 


