from flask import Flask, request, render_template, send_file, redirect, url_for, flash, session
import socket
import pandas as pd
from io import BytesIO
import os
import tempfile
from datetime import datetime
import psycopg2
import re
import concurrent.futures

app = Flask(__name__)
app.secret_key = os.urandom(24)

DATABASE_URL = "postgres://postgres.ndxdhmnabnxmgncoeoeg:6rb4A-%40ZCKCNgh%3F@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def get_whois_server(extension):
    whois_servers = {
        'com': 'whois.verisign-grs.com',
        'net': 'whois.verisign-grs.com',
        'org': 'whois.pir.org',
        'de': 'whois.denic.de',
    }
    return whois_servers.get(extension, 'whois.iana.org')

def check_domain_availability(domain, extension):
    whois_server = get_whois_server(extension)
    try:
        port = 43
        query = domain + '.' + extension + "\r\n"

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((whois_server, port))
            s.send(query.encode())
            response = s.recv(4096).decode('utf-8', errors='ignore')

        if extension == 'org':
            if "NOT FOUND" in response or "No match" in response or "Domain not found" in response:
                return True
        else:
            if "No match for" in response or "NOT FOUND" in response or "Status: free" in response:
                return True

        return False
    except Exception as e:
        print(f"Error checking domain {domain}: {e}")
        return False

def save_query_to_supabase(domain, extension, status):
    try:
        query_time = datetime.now()
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO domain_queries (domain_name, status, query_time) VALUES (%s, %s, %s)",
            (domain + '.' + extension, status, query_time)
        )
        conn.commit()
        cursor.close()
        conn.close()
        print("Query saved")
    except Exception as e:
        print(f"Error saving to Supabase: {e}")

def validate_and_split_domains(domain_input):
    invalid_chars = re.compile(r"[^a-z0-9.-]")
    turkish_chars = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
    domains = []

    for domain in domain_input.split():
        domain = domain.lower()
        if turkish_chars.search(domain):
            flash(f"Domain adında Türkçe karakterler kullanılamaz: {domain}")
            continue
        if invalid_chars.search(domain):
            flash(f"Domain adında uygun olmayan karakterler kullanılamaz: {domain}")
            continue
        domains.append(domain)

    return domains

def check_domains_async(domains, extensions):
    results = {"available": [], "unavailable": []}
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {executor.submit(check_domain_availability, domain, extension): (domain, extension) for domain in domains for extension in extensions}
        for future in concurrent.futures.as_completed(futures):
            domain, extension = futures[future]
            available = future.result()
            status = "Boş" if available else "Dolu"
            save_query_to_supabase(domain, extension, status)
            if available:
                results["available"].append({"Domain": f"{domain}.{extension}", "Durum": status})
            else:
                results["unavailable"].append({"Domain": f"{domain}.{extension}", "Durum": status})
    return results

@app.route('/', methods=['GET', 'POST'])
def index():
    print("Index route called")
    results = {"available": [], "unavailable": []}
    if request.method == 'POST':
        print("POST request received")
        domain_input = request.form['domains']
        extensions = request.form.getlist('uzantilar')
        domains = validate_and_split_domains(domain_input)

        results = check_domains_async(domains, extensions)

        df = pd.DataFrame(results["available"] + results["unavailable"])
        output = BytesIO()
        writer = pd.ExcelWriter(output, engine='openpyxl')
        df.to_excel(writer, index=False, sheet_name='Sonuçlar')
        writer.close()
        output.seek(0)

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
        with open(temp_file.name, 'wb') as f:
            f.write(output.getvalue())

        flash('Domain sorgulama işlemi tamamlandı.')
        session['temp_file'] = temp_file.name

    return render_template('index.html', results=results)

@app.route('/download')
def download():
    temp_file_path = session.get('temp_file')
    if not temp_file_path or not os.path.exists(temp_file_path):
        return redirect(url_for('index'))

    return send_file(temp_file_path, as_attachment=True, download_name="domain_results.xlsx")

@app.route('/new_query')
def new_query():
    temp_file_path = session.get('temp_file')
    if temp_file_path and os.path.exists(temp_file_path):
        os.remove(temp_file_path)
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    print("Starting server...")
    from waitress import serve
    serve(app, host='0.0.0.0', port=8000)
