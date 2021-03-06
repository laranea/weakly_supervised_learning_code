#!/usr/bin/env python

#
# This script extracts the text and code of Stack Overflow questions (not answers) in separate fields along with one-hot 
# encoded labels (folksonomy tags, 1-5 each question) for records having at least so many occurrences. To run it locally
# set PATH_SET to 'local'. For AWS using PATH_SET of 's3'.
#
# Run me with: PYSPARK_DRIVER_PYTHON=ipython3 PYSPARK_PYTHON=python3 pyspark
#

import gc
import json
import re

import boto3
from pyspark.sql import SparkSession, Row
import pyspark.sql.functions as F
from pyspark.sql.functions import udf
import pyspark.sql.types as T

from lib.utils import (
    create_labeled_schema, create_label_row_columns, extract_text, extract_text_plain, 
    extract_code_plain, get_indexes, one_hot_encode,
)


#
# Initialize Spark with dynamic allocation enabled to (hopefully) use less RAM
#
spark = SparkSession.builder\
    .appName('Weakly Supervised Learning - Extract Questions')\
    .getOrCreate()
sc = spark.sparkContext

# Load the many paths from a JSON file
PATH_SET = 's3'
PATHS = json.load(
    open('paths.json')
)

# Print debug info as we compute, takes extra time
DEBUG = True

# Print a report on record/label duplication at the end
REPORT = True


#
# Get answered questions and not their answers
#
posts = spark.read.parquet(PATHS['posts'][PATH_SET])
posts.show(3)

if DEBUG is True:
    print('Total posts count:       {:,}'.format(
        posts.count()
    ))

# Questions are posts without a parent ID
questions = posts.filter(posts.ParentId.isNull())

if DEBUG is True:
    print(
        f'Total questions count:   {questions.count():,}'
    )

# Quality questions have at least one answer and at least one vote
quality_questions = questions.filter(posts.AnswerCount > 0)\
                             .filter(posts.Score > 1)

if DEBUG is True:
    print(f'Quality questions count: {quality_questions.count():,}')

# Combine title with body
tb_questions = quality_questions.withColumn(
    'Title_Body',
    F.concat(
        F.col("Title"),
        F.lit(" "),
        F.col("Body")
    ),
)

# Split the tags and replace the Tags column
@udf(T.ArrayType(T.StringType()))
def split_tags(tag_string):
    return re.sub('[<>]', ' ', tag_string).split()

tag_questions = tb_questions.withColumn(
    'Tags',
    split_tags(
        F.col('Tags')
    )
)

# Show 5 records' Title and Tag fields, full field length
tag_questions.select('Title', 'Tags').show()

# Write all questions to a Parquet file
tag_questions\
    .write.mode('overwrite')\
    .parquet(PATHS['questions'][PATH_SET])

#
# The Big Finish(TM)!
#


#
# The remainder of this script balances the data out according to the frequency of its tags to enable a massively multi-label classifier
# to be trained on this data. This has been removed from the book but is left for reference.
#


# # Count the number of each tag
# all_tags = questions.rdd.flatMap(lambda x: re.sub('[<>]', ' ', x['Tags']).split())

# # Prepare multiple datasets with different tag count frequency filters and per-tag
# # stratified sample sizes
# for tag_limit, stratify_limit, lower_limit in \
# [
#     (50000, 50000, 500),
#     (20000, 10000, 500),
#     (10000, 10000, 500),
#     (5000, 5000, 500),
#     (2000, 2000, 500),
#     (1000, 1000, 500),
# ]:

#     print(f'\n\nStarting run for tag limit {tag_limit:,}, sample size {stratify_limit:,}, and lower limit {lower_limit:,}\n\n')

#     # Count the instances of each tag
#     tag_counts_df = all_tags\
#         .groupBy(lambda x: x)\
#         .map(lambda x: Row(tag=x[0], total=len(x[1])))\
#         .toDF()\
#         .select('tag', 'total').orderBy(['total'], ascending=False)
#     tag_counts_df.write.mode('overwrite').parquet(PATHS['tag_counts'][PATH_SET].format(tag_limit))
#     tag_counts_df = spark.read.parquet(PATHS['tag_counts'][PATH_SET].format(tag_limit))

#     if DEBUG is True:
#         tag_counts_df.show(100)

#     # Create a local dict of tag counts
#     local_tag_counts = tag_counts_df.rdd.collect()
#     tag_counts = {x.tag: x.total for x in local_tag_counts}

#     # Count the good tags
#     remaining_tags_df = tag_counts_df.filter(tag_counts_df.total > tag_limit)
#     tag_total = remaining_tags_df.count()
#     print(f'\n\nNumber of tags with > {tag_limit:,} instances: {tag_total:,}')
#     valid_tags = remaining_tags_df.rdd.map(lambda x: x['tag']).collect()

#     # Count the less frequent tags
#     bad_tags_df = tag_counts_df.filter(
#         (tag_counts_df.total <= tag_limit) & (tag_counts_df.total > lower_limit)
#     )
#     bad_tag_total = bad_tags_df.count()
#     print(f'Number of tags with >= {lower_limit:,} and lower than/equal to {tag_limit:,} instances: {bad_tag_total:,}\n\n')
#     bad_tags = bad_tags_df.rdd.map(lambda x: x['tag']).collect()

#     # Turn text of body and tags into lists of words
#     questions_lists = questions.rdd.map(
#         lambda x: (
#             extract_text_plain(x['_Body']), 
#             extract_code_plain(x['_Body']),
#             re.sub('[<>]', ' ', x['_Tags']).split()
#         )
#     )

#     # 1. Only questions with at least one tag in our list
#     # 2. Drop tags not in our list
#     filtered_lists = questions_lists\
#         .filter(lambda x: bool(set(x[2]) & set(valid_tags)))\
#         .map(lambda x: (x[0], x[1], [y for y in x[2] if y in valid_tags]))

#     #  Set aside other questions without frequent enough tags for enrichment via Snorkel
#     bad_questions = questions_lists\
#         .filter(lambda x: bool(set(x[2]) & set(bad_tags)))\
#         .map(lambda x: (x[0], x[1], [y for y in x[2] if y in bad_tags]))
#     bad_questions_df = bad_questions.map(
#         lambda x: Row(_Body=x[0], _Code=x[1], _Tags=x[2])
#     ).toDF()
#     bad_questions_df.write.mode('overwrite').parquet(
#         PATHS['bad_questions'][PATH_SET].format(tag_limit, lower_limit)
#     )
#     bad_questions_df = spark.read.parquet(
#         PATHS['bad_questions'][PATH_SET].format(tag_limit, lower_limit)
#     )

#     # Explicitly recover memory
#     del bad_tags_df
#     del questions_lists
#     del bad_questions

#     gc.collect()

#     if DEBUG is True:
#         q_count = filtered_lists.count()
#         print(f'\n\nWe are left with {q_count:,} questions containing tags with over {tag_limit:,} instances\n\n')

#     questions_tags = filtered_lists.map(lambda x: Row(_Body=x[0], _Code=x[1], _Tags=x[2])).toDF()
#     if DEBUG is True:
#         questions_tags.show()

#     # Write the word/tag lists out
#     questions_tags.write.mode('overwrite').parquet(PATHS['questions_tags'][PATH_SET].format(tag_limit))
#     questions_tags = spark.read.parquet(PATHS['questions_tags'][PATH_SET].format(tag_limit))

#     # Create forward and backward indexes for good/bad tags
#     tag_index, index_tag, enumerated_labels = get_indexes(remaining_tags_df)

#     # Explicitly free RAM
#     del remaining_tags_df
#     del bad_questions_df
#     gc.collect()

#     # One hot encode the data using one_hot_encode()
#     one_hot_questions = questions_tags.rdd.map(
#         lambda x: Row(Body=x.Body, Code=x.Code, Tags=one_hot_encode(x.Tags, enumerated_labels, index_tag))
#     )
#     if DEBUG is True:
#         print(
#             one_hot_questions.take(10)
#         )
#         # Verify we have multiple labels present
#         print(
#             one_hot_questions.sortBy(lambda x: sum(x.Tags), ascending=False).take(10)
#         )

#     # Create a DataFrame out of the one-hot encoded RDD
#     schema = T.StructType([
#         T.StructField("Body", T.StringType()),
#         T.StructField("Code", T.StringType()),
#         T.StructField("Tags", T.ArrayType(
#             T.IntegerType()
#         ))
#     ])

#     one_hot_df = spark.createDataFrame(
#         one_hot_questions,
#         schema
#     )
#     one_hot_df.show()
#     one_hot_df.write.mode('overwrite').parquet(PATHS['one_hot'][PATH_SET].format(tag_limit))
#     one_hot_df = spark.read.parquet(PATHS['one_hot'][PATH_SET].format(tag_limit))

#     one_row = one_hot_df.take(1)[0]
#     schema = create_labeled_schema(one_row)

#     # Write out a stratify_limit sized stratified sample for each tag
#     for i in range(0, tag_total):
#         print(f'\n\nProcessing tag limit: {tag_limit:,} stratify limit: {stratify_limit:,} tag {i:,} of {tag_total:,} total tags\n\n')
        
#         # Select records with a positive value for this tag
#         positive_examples = one_hot_df.rdd.filter(lambda x: x._Tags[i])
        
#         # Sample the positive examples to equal the stratify limit
#         example_count = positive_examples.count()
#         ratio = min(1.0, stratify_limit / example_count)
#         sample_ratio = max(0.0, ratio)
#         positive_examples = positive_examples.sample(False, sample_ratio, seed=1337).map(create_label_row_columns)

#         if DEBUG is True:
#             sample_count = positive_examples.count()
#             print(
#                 f'Column {i:,} had {example_count:,} positive examples, sampled to {sample_count:,}'
#             )

#         # Create a DataFrame for storing
#         output_df = spark.createDataFrame(
#             positive_examples,
#             schema
#         )

#         if DEBUG is True:
#             output_df.show()

#         # Write the record out as JSON under a directory we will then read in its enrirety
#         output_df.write.mode('overwrite').json(PATHS['output_jsonl'][PATH_SET].format(tag_limit, i))

#         # Free RAM explicitly each loop
#         del output_df
#         gc.collect()

#     # Avoid RAM problems
#     del filtered_lists
#     del one_hot_questions
#     if not REPORT:
#         del one_hot_df
#     del positive_examples
#     gc.collect()


#     #
#     # Store the associated files to local disk or S3 as JSON
#     #
#     s3 = boto3.resource('s3')

#     if PATH_SET == 's3':
#         obj = s3.Object(PATHS['s3_bucket'], PATHS['tag_index']['s3'].format(tag_limit))
#         obj.put(Body=json.dumps(tag_index).encode())

#         obj = s3.Object(PATHS['s3_bucket'], PATHS['index_tag']['s3'].format(tag_limit))
#         obj.put(Body=json.dumps(index_tag).encode())

#         obj = s3.Object(PATHS['s3_bucket'], PATHS['sorted_all_tags']['s3'].format(tag_limit))
#         obj.put(Body=json.dumps(enumerated_labels).encode())
#     else:
#         json.dump(tag_index, open(PATHS['tag_index']['local'].format(tag_limit), 'w'))
#         json.dump(tag_index, open(PATHS['index_tag']['local'].format(tag_limit), 'w'))
#         json.dump(tag_index, open(PATHS['sorted_all_tags']['local'].format(tag_limit), 'w'))


#     # Evaluate how skewed the sample is
#     stratified_sample = spark.read.json(PATHS['stratified_sample'][PATH_SET].format(tag_limit))
#     stratified_sample.registerTempTable('stratified_sample')

#     label_counts = {}

#     # I wish this could be optimized but I don't know how...
#     for i in range(0, tag_total):
#         count_df = spark.sql(f'SELECT label_{i}, COUNT(*) as total FROM stratified_sample GROUP BY label_{i}')
#         rows = count_df.rdd.take(2)
#         neg_count = getattr(rows[0], 'total')
#         pos_count = getattr(rows[1], 'total')
#         label_counts[i] = [neg_count, pos_count]

#         # Manage memory explicitly to avoid out of RAM errors
#         del count_df
#         gc.collect()

#     # Put the label counts on local disk or S3
#     if PATH_SET == 's3':
#         obj = s3.Object(PATHS['s3_bucket'], PATHS['label_counts']['s3'].format(tag_limit))
#         obj.put(Body=json.dumps(label_counts).encode())
#     else:
#         json.dump(label_counts, open(PATHS['label_counts']['local'].format(tag_limit), 'w'))

#     # Write the final stratified sample to Parquet format
#     stratified_sample.write.mode('overwrite').parquet(PATHS['questions_final'][PATH_SET].format(tag_limit))
#     stratified_sample = spark.read.parquet(PATHS['questions_final'][PATH_SET].format(tag_limit))

#     # Blow away the old stratified sample table
#     spark.catalog.dropTempView("stratified_sample")

#     #
#     # Compute a report on the data
#     #

#     if REPORT is True:

#         # Register a new table to compute duplicate ratios
#         stratified_sample.registerTempTable("final_stratified_sample")
#         raw_total = stratified_sample.count()
#         report_df = spark.sql(
#             """SELECT COUNT(*) as total FROM (SELECT DISTINCT {} FROM final_stratified_sample)""".format(
#                 ', '.join(stratified_sample.columns[1:])
#             )
#         )
#         unique_total = report_df.rdd.first().total
#         dupe_total = raw_total - unique_total
#         dupe_ratio = dupe_total * 1.0 / raw_total * 1.0

#         # Print and store a report on duplicates in the sample
#         print('Limit {tag_limit:,} has {raw_total:,} total, {unique_total:,} unique and {dupe_total:,} duplicate labelsets with a dupe ratio of {dupe_ratio:,}')

#         one_hot_original = one_hot_df.rdd.map(create_row_columns)
#         original_df = spark.createDataFrame(one_hot_original, schema)
#         original_df.registerTempTable("original_data")

#         original_raw_total = original_df.count()
#         select_cols = ', '.join(original_df.columns[1:])
#         original_report_df = spark.sql(
#             f"SELECT COUNT(*) as total FROM (SELECT DISTINCT {select_cols} FROM original_data)"
#         )
#         original_unique_total = original_report_df.rdd.first().total
#         original_dupe_total = original_raw_total - unique_total
#         original_dupe_ratio = original_dupe_total * 1.0 / original_raw_total * 1.0

#         # Print and store a report on duplicates in the original
#         print(f'Limit {tag_limit:,} originally had {original_raw_total:,} total, {original_unique_total:,} unique and {original_dupe_total:,} duplicate labelsets with a dupe ratio of {original_dupe_ratio:,}')

#         dupe_ratio_change = original_dupe_ratio - dupe_ratio
#         dupe_ratio_change_pct = dupe_ratio / original_dupe_ratio

#         print(f'Dupe ratio change raw/pct: {dupe_ratio_change:,}/{dupe_ratio_change_pct:,}')

#         report_data = {'raw_total': raw_total, 'unique_total': unique_total, 'dupe_total': dupe_total, 'dupe_ratio': dupe_ratio, 'original_raw_total': original_raw_total, 'original_unique_total': original_unique_total, 'original_dupe_total': original_dupe_total, 'original_dupe_ratio': original_dupe_ratio, 'dupe_ratio_change': dupe_ratio_change, 'dupe_ratio_change_pct': dupe_ratio_change_pct}

#         # Write the report to local disk or S3
#         if PATH_SET == 's3':
#             obj = s3.Object(PATHS['s3_bucket'], PATHS['report']['s3'].format(tag_limit))
#             obj.put(Body=json.dumps(report_data).encode())
#         else:
#             json.dump(report_data, open(PATHS['report']['local'].format(tag_limit), 'w'))
