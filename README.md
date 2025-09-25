# Dog Ear
Dog Ear automatically bookmarks PDF documents by matching user-supplied regular expressions to patterns in the text. The program requires a PDF file (or files) that already contain embedded text and a set of separate text files, each holding the desired regular expressions. For every PDF page, Dog Ear generates a numbered text file. It then applies each regular expression to every page file, compiles the matches into a table of contents file (TOC), and uses this TOC to add bookmarks to the original PDF. Bookmarks are grouped according to the name of the text file that supplied the matching regular expression. Highlights include:

* Quick creation of text files in memory
* Access to text files for testing regular expressions
* In-app text editor to modify the TOC before applying bookmarks
* Optional post-processing hook (.sh or .py) for the TOC

Dog Ear ships with sample PDFs, regular-expression text files, and a sample post-processing python script. These items are for demonstration and can easily be removed. Dog Ear works from a copy of your input PDFs that does not contain page objects. Thus, there is no need to remove existing bookmarks before adding them to the input directory.



## Creating Regular Expressions

Dog Ear uses multiline, case-sensitive, Python regular expressions. If more than one regular expression is used in a text file, each regular expression must be placed on a separate line. An empty line may be placed in between them. The most straighforward way to create a regular expression useful for bookmarking is to create a capture group like this: 

    (PATTERN)
    
Then place one or more non-capture groups around it to eliminate false positives:

    (?:PATTERN)
    
The Python regular expression engine will use all groups that are in parenthesese to find a match. However, it will only print the capturing group to the TOC file. For example, consider these groups: 

    (?:\s+California Penal Code)(?:.*\n)(\s+Section \d+)
    
`(?:\s+California Penal Code)` is a non-capturing group that matches one or more spaces and the literal text "California Penal Code" `(?:.*\n)` is another non-capturing group. It matches a newline. The capturing group, `(\s+Section \d+)` matches one or more spaces, the literal text "Section" and one or more digits. So the regular expression will print “Section 1473” only when it appears after “California Penal Code” and a single newline. 

It is possible to get far more specific. Consult your favorite large language model for assistance. I have found the following snippets useful for writing regular expressions:

* (?:.*\n+)     # one more 
