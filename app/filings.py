"""Bounded SEC submissions discovery and excerpts of official primary documents."""
import json
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import requests
from app.sec_identity import CIKS, resolve


def headers():
    email = os.getenv('SEC_CONTACT_EMAIL','').strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):
        raise ValueError('SEC contact is not configured')
    return {'User-Agent':'StockManager/1.0 '+email}


def download(url, settings, maximum):
    with requests.get(url,headers=headers(),timeout=settings.http_timeout,stream=True,allow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError('SEC document unavailable')
        content=bytearray()
        for chunk in response.iter_content(65536):
            content.extend(chunk)
            if len(content)>maximum:
                raise ValueError('SEC document exceeds size limit')
        return bytes(content)


def parse(payload,symbol,now,expected_cik=None):
    cik = expected_cik or CIKS.get(symbol)
    if cik is None or int(payload.get('cik',-1)) != cik:
        raise ValueError('SEC identity mismatch')
    recent=payload.get('filings',{}).get('recent',{})
    output=[]
    for i,form in enumerate(recent.get('form',[])[:1000]):
        if form not in {'10-Q','10-K','20-F','8-K','6-K','10-Q/A','10-K/A','20-F/A','8-K/A','6-K/A'}:
            continue
        try:
            # The SEC acceptance timestamp has an offset; filingDate alone has no time.
            at=datetime.fromisoformat(recent['acceptanceDateTime'][i].replace('Z','+00:00'))
            if at.tzinfo is None or not now-timedelta(days=7)<=at<=now:
                continue
            accn=recent['accessionNumber'][i]
            doc=recent['primaryDocument'][i]
            if not re.fullmatch(r'\d{10}-\d{2}-\d{6}',accn):
                continue
            base=f'https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace("-", "")}/'
            document=base+doc if re.fullmatch(r'[A-Za-z0-9_-]+\.(?:htm|html)',doc) else None
            output.append(dict(source_id='sec:'+accn,symbol=symbol,title=f'{symbol} ยื่นเอกสาร {form} ใหม่',
                published_at=at.isoformat(),source_name='SEC EDGAR',source_url=base+accn+'-index.html',
                document_url=document,form=form,evidence_scope='Filing metadata; content not yet read',excerpt=''))
        except (KeyError,IndexError,ValueError,TypeError):
            continue
    return output


def collect(settings,symbols,now):
    path=settings.database_path.parent/'filings.sqlite3'
    result={}
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS feeds(symbol TEXT PRIMARY KEY,at REAL,status TEXT,payload TEXT)')
    for symbol in symbols:
        cik = resolve(settings, symbol, now)
        if cik is None:
            result[symbol] = dict(status='identity_unavailable', items=[])
            continue
        with sqlite3.connect(path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT at,status,payload FROM feeds WHERE symbol=?',(symbol,)).fetchone()
            if row and now.timestamp()-row[0]<14400:
                result[symbol]=dict(status=row[1],items=json.loads(row[2]))
                continue
            db.execute('INSERT OR REPLACE INTO feeds VALUES(?,?,?,?)',(symbol,now.timestamp(),'failed','[]'))
        items,status=[],'failed'
        try:
            raw=download(f'https://data.sec.gov/submissions/CIK{cik:010d}.json',settings,5_000_000)
            items=parse(json.loads(raw),symbol,now,cik)
            status='ok'
        except (requests.RequestException,ValueError,TypeError):
            pass
        with sqlite3.connect(path) as db:
            db.execute('UPDATE feeds SET status=?,payload=? WHERE symbol=?',(status,json.dumps(items,ensure_ascii=False),symbol))
        result[symbol]=dict(status=status,items=items)
    return result


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(); self.depth=0; self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','head','ix:hidden'}:
            self.depth+=1
    def handle_endtag(self,tag):
        if tag in {'script','style','head','ix:hidden'}:
            self.depth=max(0,self.depth-1)
    def handle_data(self,data):
        if not self.depth:
            self.parts.append(data)


def add_excerpt(settings,item):
    item=dict(item)
    url=item.get('document_url') or ''
    if not re.fullmatch(r'https://www\.sec\.gov/Archives/edgar/data/\d+/\d+/[A-Za-z0-9_-]+\.(?:htm|html)',url):
        return item
    try:
        content=download(url,settings,3_000_000)
        parser=VisibleText();parser.feed(content.decode('utf-8',errors='replace'))
        text=' '.join(' '.join(parser.parts).split())
        item['excerpt']=text[:4500]
        item['evidence_scope']='Beginning excerpt of SEC primary document only; attachments and remainder not read'
    except (requests.RequestException,ValueError):
        item['evidence_scope']='SEC primary document unavailable; metadata only'
    return item
