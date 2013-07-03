#!/usr/bin/python
# -*- coding: utf-8 -*-

import sys
import os
import csv
import logging
import tempfile
import traceback
import urllib
import codecs
import json
import platform
import pickle
from datetime import datetime, timedelta
from lib.utils import *
import re
from collections import Counter, defaultdict
from lib.stemutil import getLanguage
from org.papermachines.util import NgramTokenizer
reload(sys)
sys.setdefaultencoding('utf-8')

class TextProcessor:

    """
    Base class for text processing in Paper Machines
    """

    def __init__(self, track_progress=True):
        self.sys = platform.system()

        # take in command line options

        self.arg_filename = sys.argv[1]
        self.arg_basename = os.path.basename(sys.argv[1]).replace('.json', '')

        with codecs.open(self.arg_filename, 'r', encoding='utf-8') as arg_file:
            args = json.load(arg_file)

        self.cwd = args[0]
        csv_file = args[1]
        self.out_dir = args[2]
        self.collection_name = args[3]
        self.extra_args = args[4:]

        if 'json' in self.extra_args:
            json_starts_at = self.extra_args.index('json')
            self.named_args = json.loads(self.extra_args[json_starts_at
                    + 1])
            self.extra_args = self.extra_args[:json_starts_at]
        else:
            self.named_args = {}

        self.collection = os.path.basename(csv_file).replace('.csv', '')

        self.require_stopwords = True  # load stopwords by default

        # call a function to set processor name, etc.

        self._basic_params()

        if self.require_stopwords:
            self.stoplist = os.path.join(self.cwd, 'stopwords.txt')
            self.stopwords = [x.strip() for x in
                              codecs.open(self.stoplist, 'r',
                              encoding='utf-8').readlines()
                              if x.strip() != '']

        self.out_filename = os.path.join(self.out_dir, self.name
                + self.collection + '-' + self.arg_basename + '.html')

        logging.basicConfig(filename=self.out_filename.replace('.html',
                            '.log'), filemode='w', level=logging.INFO)

        fh = logging.FileHandler(os.path.join(self.out_dir, 'logs',
                                 self.name + '.log'))
        formatter = \
            logging.Formatter('%(asctime)s: %(levelname)-8s %(message)s'
                              )
        fh.setFormatter(formatter)

        logging.getLogger('').addHandler(fh)

        logging.info('command: ' + ' '.join([x.replace(' ', '''\ ''')
                     for x in sys.argv]))

        self.metadata = {}

        for rowdict in parse_csv(csv_file):
            filename = rowdict.pop('filename')
            self.metadata[filename] = rowdict

        self.files = self.metadata.keys()
        if track_progress:
            self.track_progress = True
            self.progress_initialized = False
        self.post_setup()

    def post_setup(self):
        pass

    def _basic_params(self):
        self.name = 'textprocessor'

    def update_progress(self):
        if self.track_progress:
            if not self.progress_initialized:
                self.progress_filename = os.path.join(self.out_dir,
                        self.name + self.collection + 'progress.txt')
                self.progress_file = file(self.progress_filename, 'w')
                self.count = 0
                self.total = len(self.files)
                self.progress_initialized = True
            self.count += 1
            self.progress_file.write('<' + str(int(self.count * 1000.0
                    / float(self.total))) + '>\n')
            self.progress_file.flush()

    def get_mtime(self, filename):
        t = os.path.getmtime(filename)
        return datetime.fromtimestamp(t)

    def older(self, old_file, new_file):
        return self.get_mtime(old_file) > self.get_mtime(new_file)

    def getNgrams(self, filename, n=1, stemming=False):
        tokenizer_name = "tokenizer" + str(n) + ('stemming' if stemming else '')
        tokenizer = getattr(self, tokenizer_name, None)
        if tokenizer is None:
            stem_language = ""
            if stemming:
                lang_code = getattr(self, "lang", "en")
                stemLanguage = getLanguage(lang_code)
            tokenizer = NgramTokenizer(n, stemLanguage, self.stopwords)
            setattr(self, tokenizer_name, tokenizer)
        return tokenizer.tokenize(filename)

    def get_doc_date(self, filename):
        doc_date = None
        date_str = self.metadata[filename]['date']
        if date_str.strip() != '':
            cleaned_date = date_str[0:10]
            if '-00' in cleaned_date:
                cleaned_date = cleaned_date[0:4] + '-01-01'
            try:
                doc_date = datetime.strptime(cleaned_date, '%Y-%m-%d')
            except:
                pass
        return doc_date

    def split_into_intervals(self, start_and_end_dates=False):
        self.start_date = getattr(self, "start_date", None)
        self.end_date = getattr(self, "end_date", None)
        datestr_to_datetime = {}
        for filename in self.metadata.keys():
            date_str = self.metadata[filename]['date']
            date_for_doc = self.get_doc_date(filename)
            if date_for_doc is None:
                logging.error(("File {:} has invalid date" +
                              "-- removing...").format(filename))
                del self.metadata[filename]
                continue
            datestr_to_datetime[date_str] = date_for_doc
            if (self.start_date is not None and 
                    date_for_doc < self.start_date):
                logging.error(("File {:} is before date range" +
                               "-- removing...").format(filename))
                del self.metadata[filename]
                continue
            if (self.end_date is not None and 
                    date_for_doc > self.end_date):
                logging.error(("File {:} is after date range" +
                               "-- removing...").format(filename))
                del self.metadata[filename]
                continue
        datetimes = sorted(datestr_to_datetime.values())
        start_date = (datetimes[0] if self.start_date
                      is None else self.start_date)
        end_date = (datetimes[-1] if self.end_date
                    is None else self.end_date)

        self.interval = getattr(self, "interval", 1)
        interval = timedelta(self.interval)

        self.intervals = []
        self.interval_names = []
        start = end = start_date
        while end <= end_date:
            end += interval
            self.intervals.append((start, end))
            if start_and_end_dates:
                self.interval_names.append(
                    start.isoformat()[0:10].replace('-', '/') + 
                    '-' + end.isoformat()[0:10].replace('-', '/')
                )
            else:
                self.interval_names.append(start.isoformat())
            start = end

        self.labels = getattr(self, "labels", defaultdict(set))
        for (filename, metadata) in self.metadata.iteritems():
            label = ''
            for i in range(len(self.intervals)):
                interval = self.intervals[i]
                dt = datestr_to_datetime[metadata['date']]
                if (interval[0] <= dt and 
                        dt < interval[1]):
                    label = self.interval_names[i]
                    break
            self.labels[label].add(filename)

    def write_html(self, data_params):
        logging.info('writing HTML')
        self.data_filename = self.out_filename.replace('.html', '.js')
        html_params = {'COLLECTION_NAME': self.collection_name,
                       'DATA_PATH': os.path.basename(self.data_filename)}
        data_params.update({'DOC_METADATA': dict((v['itemID'], v)
                           for (k, v) in self.metadata.iteritems())})
        try:
            template_filename = getattr(self, 'template_filename',
                    os.path.join(self.cwd, 'templates', self.name
                    + '.html'))

            with codecs.open(self.out_filename, 'w', encoding='utf-8'
                             ) as outfile:
                with codecs.open(template_filename, 'r',
                                 encoding='utf-8') as template:
                    template_str = template.read()
                    for (k, v) in html_params.iteritems():
                        template_str = template_str.replace(k, v)
                    outfile.write(template_str)
            with codecs.open(self.data_filename, 'w', encoding='utf-8'
                             ) as datafile:
                datafile.write('var data=')
                json.dump(data_params, datafile)
                datafile.write(';')
        except:
            logging.error(traceback.format_exc())

    def process(self):
        """
        Example process -- should be overridden
        """

        output = file(os.path.join(self.out_dir, self.name + '.txt'),
                      'w')
        for filename in self.files:
            output.write(' '.join([filename, self.metadata[filename]])
                         + '\n')
        output.close()


if __name__ == '__main__':
    try:
        processor = TextProcessor(track_progress=True)
        processor.process()
    except:
        logging.error(traceback.format_exc())
