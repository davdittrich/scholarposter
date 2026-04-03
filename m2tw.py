#! /usr/bin/env python3

from mastodon import Mastodon
import tweepy
from tweepy.errors import TweepyException
import config

import math
import textwrap
import json
import requests
from datetime import datetime
from ln_oauth import auth, headers
from math import sqrt
import html
import re
import io
import doi
import pdfplumber
from pikepdf import Pdf
from pdftitle import get_title_from_io as getPDFtitle
from bs4 import BeautifulSoup
import mimetypes
from sumy.parsers.html import HtmlParser
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.kl import KLSummarizer as Summarizer2
from sumy.summarizers.lsa import LsaSummarizer as Summarizer
from sumy.nlp.stemmers import Stemmer
from sumy.utils import get_stop_words
import cloudscraper
from os import path

LANGUAGE = "english"

mastodon = Mastodon(
    access_token='pytooter_usercred.secret',
    api_base_url='https://fediscience.org'
)


def _get_title(soup):
    """
    Extracting the title of the page
    We take the text of the title tag or the first h1 tag
    """
    if (soup.title and soup.title.string != ''):
        return soup.title.string
    if soup.find("meta", property="og:title"):
        return soup.find("meta", property="og:title")["content"]
    if (soup.h1 and soup.h1.string != ''):
        return soup.h1.string
    return None


def _get_description(soup):
    """
    Description of the page, the text following the h1 tag
    or just the first p tag
    """
    if soup.find("meta", property="og:description"):
        return soup.find("meta", property="og:description")["content"]
    first_h1 = soup.find('h1')
    if first_h1:
        first_p = first_h1.find_next('p')
        if (first_p and first_p.string != ''):
            return first_p.string
    first_p = soup.find('p')
    if (first_p and first_p.string != ''):
        return first_p.string
    return None


def getSummarytext(url):
    parser = PlaintextParser.from_string(url, Tokenizer(LANGUAGE))
    stemmer = Stemmer(LANGUAGE)
    summ = Summarizer(stemmer)
    summ2 = Summarizer2(stemmer)
    summ.stop_words = get_stop_words(LANGUAGE)
    summ2.stop_words = get_stop_words(LANGUAGE)
    fullText = ""
    sc = len(parser.document.sentences)
    if sc < 6:
        return ""
    if sc > 150:
      print("Shorten the FullText Stage 1:", sc, "\n")
      for sentence in summ(parser.document, max(150, int(150+sqrt(sc-150)))):
        if len(str(sentence)) > 40:
          fullText += str(sentence) + " "
      fullText = re.sub(r"\([^()]*\)", "", fullText)
      parser = PlaintextParser.from_string(fullText, Tokenizer(LANGUAGE))
      sc = len(parser.document.sentences)
      print("Shorten the FullText Stage 2:", sc, "\n")
      fullText = ""
    pc = len(parser.document.paragraphs)
    nos = min(max(3, int(0.01*sc), int(0.05*pc)), 5)
    for sentence in summ2(parser.document, nos):
      if len(str(sentence)) > 40:
        fullText += str(sentence) + " "
    fullText = re.sub(r"\([^()]*\)", "", fullText)
    while len(fullText) > 1000:
      nos=nos - 1
      if nos == 0:
        break
      fullText=""
      for sentence in summ2(parser.document, nos):
        fullText += str(sentence) + " "
      fullText = re.sub(r"\([^()]*\)", "", fullText)
    if len(fullText) > 1000:
       fullText = fullText.ljust(1000)[:1000] + u"\u2026"
    return fullText


def guess_type_of(link, strict=True):
    link_type, _ = mimetypes.guess_type(link)
    if link_type is None and strict:
      try:
        print("trying requests.get\n")
        u = requests.get(link, verify=False, timeout=10)
        link_type = u.headers['Content-Type']
      except:
        print("URL does not exist on WWW")
        link_type = ""
    return link_type


def unshorten_url(url):
    return requests.head(url, allow_redirects=True).url


# Add the user Id you want to get tweets
user_id="109276496085205920"
# Add the number of tweets you want to get
number_of_tweets=50
since_id=1109449987640976370
#since_id=109300309877736194

try:
    ltfile=open('lasttoot-tw.txt', "r")
    since_id=ltfile.readline() 
    since_id=int(since_id) + 1
    ltfile.close()
except:
    print("File does not exist\n")


print(since_id,"\n")

cred = mastodon.me()
timeline = mastodon.account_statuses(id=cred.id, only_media=False, pinned=False, exclude_replies=True, exclude_reblogs=False, min_id=since_id, limit=50)

# for tweet in timeline:
tweet=list(timeline)[-1]
log_id = tweet.id

#print(tweet,"\n")

org_name = ""

if (tweet.reblog):
#  id = tweet.reblog.id
  tweet = tweet.reblog
  org_name = tweet.account.display_name
  org_sname = tweet.account.acct


soup1 = BeautifulSoup(tweet.content, 'html5lib')
for match in soup1.findAll('span'):
    match.unwrap()
for match in soup1.findAll('a'):
    match.unwrap()
soup1= BeautifulSoup(str(soup1), 'html5lib')
soup1= soup1.get_text("\n")


message = str(soup1).replace(" ,", ",").replace("  ", " ")
message=(message) + "\n\norig " + str(tweet.url)
#pattern=r'(?i)\b((?:[a-z][\w-]+:(?:/{1,3}|[a-z0-9%])|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:\'".,<>?«»“”‘’]))';
#match = re.findall(pattern, message)
#for m in match:
#    url = m[0]
#    message = message.replace(url, unshorten_url(url))
if len(org_name) > 1:
    org_name = re.sub(r':.+?:','', org_name)
#  message = "via " + org_name + " (" + '\u0040' + org_sname + ")\n" + message
    message = "via " + org_name.strip() + ":\n" + message

print("Text: ", message, "\n")


urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', str(soup1))


eurl=None
try:
  for url in urls:
    eurl=url
except:
  print("eurl Error\n")

urldesc=""
summary=""
if eurl:
  if "repec" in eurl:
    try:
      eurl = unshorten_url(eurl)
      urldesc = eurl
    except:
      print("RePec Error\n")

  if "doi.org" in url:
    try:
      udoi =  re.sub(r'https://doi.org/', '', url) 
      eurl = doi.get_real_url_from_doi(udoi)
    except:
      print("DOI Error\n")
  
  ltype = guess_type_of(eurl)
  if 'text/html' in ltype:
    url = unshorten_url(eurl)
    scraper =  cloudscraper.create_scraper()
    try:
      reqs = scraper.get(url)
    except:
      print("scraper.get does not get ", url, "\n")
      try:
        reqs = requests.get(url, verify=False, timeout=10)
      except:
        print("request.get does not get ", url, "\n")
    # using the BeaitifulSoup module
    try:
      soup = BeautifulSoup(reqs.text, 'lxml')
      text = soup.get_text()
    except:
      print("Problems parsing html\n")
    try:
      urldesc= html.unescape(_get_title(soup))
    except:
      print("No title found\n")
#    try:
#      summary=re.sub(r'\s([?.!"](?:\s|$))', r'\1', getSummarytext(text)).strip()
#      if len(summary) <= 1:
#        summary = html.unescape(_get_description(soup))
#    except:
#      print("No summary found\n")

  if '.pdf' in eurl:
    if 'pdf' in ltype:
      try:
        scraper =  cloudscraper.create_scraper()
        reqs = scraper.get(url)
        f = io.BytesIO(reqs.content)
        pdf = Pdf.open(f)
        meta = pdf.open_metadata()
      except:
        print("cannot get pdf\n")
      title = None
      abstract = ""
      try:
        title = meta['dc:title']
        abstract = meta['dc:description']
      except:
        print("metadata error")
      if not title:
        try:
          title = getPDFtitle(f)
        except:
          print("title error")
      text = ""
      if title:
        urldesc=title
        text += title + "\n\n"
      if abstract:
        text += abstract + "\n\n"
      try:
        pdf = pdfplumber.open(f)
        for page in pdf.pages:
          if page.extract_text():
            text += page.extract_text()
      except:
        print("pdf error")
      text = text.rsplit('References', 1)[0]
      text = text.replace('\r', ' ')
      text = re.sub(r'[^\x00-\x7f]',r' ',text)
      text = re.sub(r"[^\S\n\t]+"," ",text)

print(eurl, ": eurl\n")

mediaurl=None
mediadesc=None
try:
  for media in tweet.media_attachments:
    if media['remote_url'] != None:
      mediaurl= media['remote_url']
    else:
      mediaurl= media['url']
    mediadesc = media['description']
except:
  print("mediaurl error\n")

if summary: 
  message = ''.join([message,"\n\n tldr:\n", summary])


print(mediaurl,"\n")
print(urldesc,"\n")


### Twitter

from dotenv import load_dotenv
load_dotenv()

client = tweepy.Client(
    consumer_key=config.consumer_key, 
    consumer_secret=config.consumer_secret,
    access_token=config.access_token, 
    access_token_secret=config.access_token_secret
)

auth = tweepy.OAuth1UserHandler(
    config.consumer_key, 
    config.consumer_secret, 
    config.access_token, 
    config.access_token_secret
)

#bearer_token = "AAAAAAAAAAAAAAAAAAAAADW%2F3gAAAAAA%2FZjgGPIhlJCxZsK6SPc1%2BQjyyRg%3DxGnXdkXebRVxvhTnuYiPgTKHnPaBmHL5Nlo4wUF5WzTzrpXWQU"

#auth= tweepy.AppAuthHandler(cid, csec)
api = tweepy.API(auth)

# message
# obtain length of tweet 
tweet_length = len(message)

# check length
if tweet_length < 274:
  tweet_length_limit = 274

elif tweet_length >= 274:
  # divided tweet_length / 280
  # You might consider adjusting this down 
  # depending on how you want to format the 
  # tweet.

  if re.search(r'\S\.\S', message):
    if re.search(r'http', message):
      tweet_length_limit = 265
    else:
      tweet_length_limit = 260
  else:
    tweet_length_limit = 265

# determine the number of tweets 
tweet_chunk_length = tweet_length_limit
tweet_count = math.ceil(tweet_length / tweet_chunk_length)

# chunk the tweet into individual pieces
tweet_chunks = textwrap.wrap(message,  tweet_chunk_length, break_long_words=False, break_on_hyphens=False, replace_whitespace=False )

# iterate over the chunks 
x = 1
for chunk in (tweet_chunks):
  if x == 1:
    if tweet_count > 1:
      part = ''.join([chunk," … ", str(x), "/" ,str(tweet_count)])
    else:
      part = chunk
  
    part = part.strip() 
    print("Chunk ", x, ": ", len(part)) 

    if mediaurl:
      r = requests.get(mediaurl, verify=False, timeout=10)
      f_type = r.headers['Content-Type']
      f_name = path.basename(mediaurl)
      if r.status_code == requests.codes.ok:  # image returned OK
        try:
            f = io.BytesIO(r.content)
            response= api.media_upload(filename=f_name, file=f)
            api.create_media_metadata(media_id=response.media_id, alt_text=mediadesc)
#            result= api.update_status(status=part, media_ids=[response.media_id])
            result = client.create_tweet(text=part, media_ids=[response.media_id])
            f.close()
        except TweepyException as e:
            print("API Error\n")
            print(e,"\n")
#            result= api.update_status(status=part)  
            result = client.create_tweet(text=part)
      else:
        print("error fetching image\n")
#        result= api.update_status(status=part)
        result = client.create_tweet(text=part)
      r.close()
    else:  
#      result= api.update_status(status=part)
      result = client.create_tweet(text=part)
  
  else:
    if x == tweet_count:
      part = ''.join([chunk," ", str(x), "/" ,str(tweet_count)])
    else:
      part = ''.join([chunk," … ", str(x), "/" ,str(tweet_count)])
    
    part = part.strip() 
    print("Chunk ", x, ": ", len(part)) 
#    result= api.update_status(status=part, in_reply_to_status_id=tweetid)
    result = client.create_tweet(text=part, in_reply_to_tweet_id=tweetid)

  print(part, "\n")
  x = x+1

      
  tweetid=result.data['id']
  print(tweetid, "\n")



###
fout = open('mastodon2twitter.log', 'a')
out = ' '.join([datetime.now().strftime("%m/%d/%Y, %H:%M:%S"), " ", str(tweetid), " ", str(tweet.id), "\n"])
fout.write(out)
fout.close()
#except:
#  print("Logfile not written\n")

ltfile = open('lasttoot-tw.txt','w')
ltfile.write(str(log_id))
ltfile.close()